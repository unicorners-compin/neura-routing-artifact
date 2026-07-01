#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-helper.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/tcp-socket-base.h"

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>

using namespace ns3;

namespace {

struct ExperimentConfig
{
    std::string method{"neura"};
    std::string summaryJson;
    std::string timelineCsv;
    std::string cwndCsv;
    double linkRateMbps{10.0};
    double linkDelayMs{2.0};
    uint32_t queuePackets{20};
    double sampleMs{100.0};
    double ospfPeriodMs{500.0};
    double simSeconds{12.0};
    double backgroundRateMbps{8.0};
    double baseCostA{0.00};
    double baseCostB{0.05};
    double triggerThreshold{0.18};
    double hysteresis{0.03};
    double liftThreshold{0.22};
    double liftGuard{0.06};
    double liftMemoryDecay{0.90};
    double liftMemoryGain{0.25};
    double liftAccumDecay{0.12};
    double liftAccumGain{0.60};
    double liftRefractoryMs{700.0};
    double minSwitchMs{200.0};
    double controllerStartSeconds{1.5};
};

struct ControllerState
{
    std::string selectedPath{"a"};
    double memoryA{0.0};
    double memoryB{0.0};
    double accum{0.0};
    double lastSwitchTime{-1e9};
    double nextAllowedSwitch{-1e9};
    uint32_t routeChanges{0};
    uint32_t tailSwitches{0};
};

struct PathState
{
    Ptr<PointToPointNetDevice> srcDevice;
    Ipv4Address nextHop;
    uint32_t interfaceIndex{0};
};

struct TcpTraceState
{
    bool attached{false};
    bool seenFirst{false};
    uint32_t lastCwnd{0};
    uint32_t minCwnd{std::numeric_limits<uint32_t>::max()};
    uint32_t maxCwnd{0};
    uint32_t dropEvents{0};
    std::ofstream cwnd;
};

struct RxCheckpointState
{
    uint64_t rxAt2{0};
    uint64_t rxAt5{0};
    uint64_t rxAt7{0};
    uint64_t rxAt10{0};
    uint64_t rxAt12{0};
};

struct TopologyState
{
    Ptr<Node> source;
    Ptr<Node> relayA;
    Ptr<Node> relayB;
    Ptr<Node> dest;
    Ptr<Node> bgSink;
    PathState pathA;
    PathState pathB;
    Ptr<PacketSink> primarySink;
    Ptr<PacketSink> bgSinkApp;
    Ptr<BulkSendApplication> primaryBulkApp;
    Ptr<FlowMonitor> flowMonitor;
    FlowMonitorHelper flowHelper;
    ControllerState controller;
    TcpTraceState tcpTrace;
    RxCheckpointState rx;
    ExperimentConfig config;
    std::ofstream timeline;
    Ipv4Address destAddress;
    Ipv4Address bgSinkAddress;
    uint16_t primaryPort{50000};
    uint16_t bgPort{50001};
};

double Clamp01(double value)
{
    if (value < 0.0)
    {
        return 0.0;
    }
    if (value > 1.0)
    {
        return 1.0;
    }
    return value;
}

std::string MbpsString(double value)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3) << value << "Mbps";
    return out.str();
}

bool BurstActive(double now)
{
    return (now >= 2.0 && now < 5.0) || (now >= 7.0 && now < 10.0);
}

double QueueNorm(const Ptr<PointToPointNetDevice>& dev, uint32_t queuePackets)
{
    auto queue = dev->GetQueue();
    if (queue == nullptr || queuePackets == 0)
    {
        return 0.0;
    }
    return Clamp01(static_cast<double>(queue->GetNPackets()) / static_cast<double>(queuePackets));
}

Ptr<Ipv4StaticRouting> GetStaticRouting(Ptr<Node> node)
{
    auto ipv4 = node->GetObject<Ipv4>();
    Ipv4StaticRoutingHelper helper;
    return helper.GetStaticRouting(ipv4);
}

void ReplaceSourceRoute(TopologyState* state, const std::string& newPath)
{
    auto routing = GetStaticRouting(state->source);
    const auto dest = state->destAddress;
    for (int i = static_cast<int>(routing->GetNRoutes()) - 1; i >= 0; --i)
    {
        auto route = routing->GetRoute(static_cast<uint32_t>(i));
        if (route.IsHost() && route.GetDest() == dest)
        {
            routing->RemoveRoute(static_cast<uint32_t>(i));
        }
    }
    const auto& path = (newPath == "a") ? state->pathA : state->pathB;
    routing->AddHostRouteTo(dest, path.nextHop, path.interfaceIndex);
    state->controller.selectedPath = newPath;
}

void RecordSample(TopologyState* state, double qA, double qB, bool burstActive)
{
    const double now = Simulator::Now().GetSeconds();
    state->timeline << std::fixed << std::setprecision(6) << now << "," << state->controller.selectedPath << ","
                    << qA << "," << qB << "," << state->controller.accum << "," << state->controller.memoryA << ","
                    << state->controller.memoryB << "," << (burstActive ? 1 : 0) << "\n";
}

void MaybeSwitch(TopologyState* state, const std::string& nextPath, double now)
{
    if (nextPath == state->controller.selectedPath)
    {
        return;
    }
    if (now < state->controller.nextAllowedSwitch)
    {
        return;
    }
    ReplaceSourceRoute(state, nextPath);
    state->controller.routeChanges += 1;
    if (!BurstActive(now))
    {
        state->controller.tailSwitches += 1;
    }
    state->controller.lastSwitchTime = now;
    if (state->config.method == "neura" || state->config.method == "lift")
    {
        state->controller.nextAllowedSwitch = now + state->config.liftRefractoryMs / 1000.0;
    }
    else
    {
        state->controller.nextAllowedSwitch = now + state->config.minSwitchMs / 1000.0;
    }
}

void ControllerStep(TopologyState* state)
{
    const double now = Simulator::Now().GetSeconds();
    const double qA = QueueNorm(state->pathA.srcDevice, state->config.queuePackets);
    const double qB = QueueNorm(state->pathB.srcDevice, state->config.queuePackets);
    const bool burst = BurstActive(now);

    auto& ctl = state->controller;
    ctl.memoryA = ctl.memoryA * state->config.liftMemoryDecay + state->config.liftMemoryGain * qA;
    ctl.memoryB = ctl.memoryB * state->config.liftMemoryDecay + state->config.liftMemoryGain * qB;

    const double currentQ = (ctl.selectedPath == "a") ? qA : qB;
    const double altQ = (ctl.selectedPath == "a") ? qB : qA;
    const double currentBase = (ctl.selectedPath == "a") ? state->config.baseCostA : state->config.baseCostB;
    const double altBase = (ctl.selectedPath == "a") ? state->config.baseCostB : state->config.baseCostA;
    const double currentMemory = (ctl.selectedPath == "a") ? ctl.memoryA : ctl.memoryB;
    const double altMemory = (ctl.selectedPath == "a") ? ctl.memoryB : ctl.memoryA;

    if (state->config.method == "neura" || state->config.method == "lift")
    {
        const double diff =
            (currentBase + currentQ + 0.35 * currentMemory) - (altBase + altQ + 0.35 * altMemory);
        ctl.accum = std::max(0.0, ctl.accum * (1.0 - state->config.liftAccumDecay) +
                                      state->config.liftAccumGain * diff);
        const double effCurrent = currentBase + currentQ + 0.45 * currentMemory;
        const double effAlt = altBase + altQ + 0.45 * altMemory;
        if (ctl.accum >= state->config.liftThreshold && effAlt + state->config.liftGuard < effCurrent)
        {
            MaybeSwitch(state, ctl.selectedPath == "a" ? "b" : "a", now);
            ctl.accum = 0.0;
        }
    }
    else if (state->config.method == "triggered_te")
    {
        ctl.accum = 0.0;
        const double currentCost = currentBase + currentQ;
        const double altCost = altBase + altQ;
        const double margin = burst ? state->config.triggerThreshold : state->config.hysteresis;
        if (altCost + margin < currentCost)
        {
            MaybeSwitch(state, ctl.selectedPath == "a" ? "b" : "a", now);
        }
    }
    else
    {
        ctl.accum = 0.0;
        if (now - ctl.lastSwitchTime >= state->config.ospfPeriodMs / 1000.0)
        {
            const std::string better =
                (state->config.baseCostA + qA <= state->config.baseCostB + qB) ? "a" : "b";
            MaybeSwitch(state, better, now);
        }
    }

    RecordSample(state, qA, qB, burst);
    const double nextDelay = state->config.sampleMs / 1000.0;
    if (now + nextDelay <= state->config.simSeconds + 1e-9)
    {
        Simulator::Schedule(Seconds(nextDelay), &ControllerStep, state);
    }
}

void OnCwndChange(TopologyState* state, uint32_t oldValue, uint32_t newValue)
{
    const double now = Simulator::Now().GetSeconds();
    if (!state->tcpTrace.seenFirst)
    {
        state->tcpTrace.seenFirst = true;
        state->tcpTrace.lastCwnd = newValue;
        state->tcpTrace.minCwnd = newValue;
        state->tcpTrace.maxCwnd = newValue;
    }
    else
    {
        if (newValue < oldValue)
        {
            state->tcpTrace.dropEvents += 1;
        }
        state->tcpTrace.lastCwnd = newValue;
        state->tcpTrace.minCwnd = std::min(state->tcpTrace.minCwnd, newValue);
        state->tcpTrace.maxCwnd = std::max(state->tcpTrace.maxCwnd, newValue);
    }
    if (state->tcpTrace.cwnd.is_open())
    {
        state->tcpTrace.cwnd << std::fixed << std::setprecision(6) << now << "," << oldValue << "," << newValue << "\n";
    }
}

void AttachTcpTrace(TopologyState* state)
{
    if (state->tcpTrace.attached || state->primaryBulkApp == nullptr)
    {
        return;
    }
    auto socket = DynamicCast<TcpSocketBase>(state->primaryBulkApp->GetSocket());
    if (socket == nullptr)
    {
        Simulator::Schedule(MilliSeconds(50), &AttachTcpTrace, state);
        return;
    }
    state->tcpTrace.attached = true;
    socket->TraceConnectWithoutContext("CongestionWindow", MakeBoundCallback(&OnCwndChange, state));
}

void RecordRxCheckpoint(TopologyState* state, int slot)
{
    const uint64_t rx = state->primarySink->GetTotalRx();
    switch (slot)
    {
    case 2:
        state->rx.rxAt2 = rx;
        break;
    case 5:
        state->rx.rxAt5 = rx;
        break;
    case 7:
        state->rx.rxAt7 = rx;
        break;
    case 10:
        state->rx.rxAt10 = rx;
        break;
    case 12:
        state->rx.rxAt12 = rx;
        break;
    default:
        break;
    }
}

void WriteSummary(TopologyState& state)
{
    auto classifier = DynamicCast<Ipv4FlowClassifier>(state.flowHelper.GetClassifier());
    const auto stats = state.flowMonitor->GetFlowStats();

    uint64_t primaryTxBytes = 0;
    uint64_t primaryRxBytes = 0;
    double primaryDelaySum = 0.0;
    uint64_t primaryRxPackets = 0;
    uint64_t bgTxBytes = 0;
    uint64_t bgRxBytes = 0;

    for (const auto& [id, st] : stats)
    {
        auto tuple = classifier->FindFlow(id);
        if (tuple.destinationPort == state.primaryPort)
        {
            primaryTxBytes += st.txBytes;
            primaryRxBytes += st.rxBytes;
            primaryDelaySum += st.delaySum.GetSeconds();
            primaryRxPackets += st.rxPackets;
        }
        else if (tuple.destinationPort == state.bgPort)
        {
            bgTxBytes += st.txBytes;
            bgRxBytes += st.rxBytes;
        }
    }

    const double primaryDelivery =
        primaryTxBytes == 0 ? 0.0 : static_cast<double>(primaryRxBytes) / static_cast<double>(primaryTxBytes);
    const double bgDelivery =
        bgTxBytes == 0 ? 0.0 : static_cast<double>(bgRxBytes) / static_cast<double>(bgTxBytes);
    const double meanDelayMs =
        primaryRxPackets == 0 ? 0.0 : (primaryDelaySum * 1000.0) / static_cast<double>(primaryRxPackets);
    const double overallGoodputMbps =
        (static_cast<double>(primaryRxBytes) * 8.0) / (state.config.simSeconds * 1'000'000.0);
    const uint64_t burstBytes = (state.rx.rxAt5 - state.rx.rxAt2) + (state.rx.rxAt10 - state.rx.rxAt7);
    const double burstGoodputMbps = (static_cast<double>(burstBytes) * 8.0) / (6.0 * 1'000'000.0);

    std::ofstream out(state.config.summaryJson);
    out << std::fixed << std::setprecision(6);
    out << "{\n";
    out << "  \"method\": \"" << state.config.method << "\",\n";
    out << "  \"queue_packets\": " << state.config.queuePackets << ",\n";
    out << "  \"sample_ms\": " << state.config.sampleMs << ",\n";
    out << "  \"primary_delivery_ratio\": " << primaryDelivery << ",\n";
    out << "  \"primary_mean_delay_ms\": " << meanDelayMs << ",\n";
    out << "  \"primary_tx_bytes\": " << primaryTxBytes << ",\n";
    out << "  \"primary_rx_bytes\": " << primaryRxBytes << ",\n";
    out << "  \"primary_overall_goodput_mbps\": " << overallGoodputMbps << ",\n";
    out << "  \"primary_burst_goodput_mbps\": " << burstGoodputMbps << ",\n";
    out << "  \"background_delivery_ratio\": " << bgDelivery << ",\n";
    out << "  \"background_tx_bytes\": " << bgTxBytes << ",\n";
    out << "  \"background_rx_bytes\": " << bgRxBytes << ",\n";
    out << "  \"route_changes\": " << state.controller.routeChanges << ",\n";
    out << "  \"tail_switches\": " << state.controller.tailSwitches << ",\n";
    out << "  \"tcp_cwnd_drop_events\": " << state.tcpTrace.dropEvents << ",\n";
    out << "  \"tcp_cwnd_min_bytes\": "
        << (state.tcpTrace.seenFirst ? state.tcpTrace.minCwnd : 0) << ",\n";
    out << "  \"tcp_cwnd_max_bytes\": " << state.tcpTrace.maxCwnd << ",\n";
    out << "  \"final_selected_path\": \"" << state.controller.selectedPath << "\"\n";
    out << "}\n";
}

int MainBody(int argc, char* argv[])
{
    ExperimentConfig config;
    CommandLine cmd(__FILE__);
    cmd.AddValue("method", "neura, triggered_te, ospf_te, or legacy lift", config.method);
    cmd.AddValue("summaryJson", "Output summary JSON path", config.summaryJson);
    cmd.AddValue("timelineCsv", "Output controller timeline CSV path", config.timelineCsv);
    cmd.AddValue("cwndCsv", "Output TCP cwnd CSV path", config.cwndCsv);
    cmd.AddValue("linkRateMbps", "Link rate in Mbps", config.linkRateMbps);
    cmd.AddValue("linkDelayMs", "Link delay in ms", config.linkDelayMs);
    cmd.AddValue("queuePackets", "DropTail queue depth in packets", config.queuePackets);
    cmd.AddValue("sampleMs", "Controller sample period in ms", config.sampleMs);
    cmd.AddValue("ospfPeriodMs", "Periodic recompute period for ospf-style baseline", config.ospfPeriodMs);
    cmd.AddValue("simSeconds", "Total simulation time in seconds", config.simSeconds);
    cmd.AddValue("backgroundRateMbps", "Burst background UDP flow rate", config.backgroundRateMbps);
    cmd.Parse(argc, argv);

    if (config.summaryJson.empty() || config.timelineCsv.empty() || config.cwndCsv.empty())
    {
        NS_FATAL_ERROR("summaryJson, timelineCsv, and cwndCsv are required");
    }

    Config::SetDefault("ns3::TcpL4Protocol::SocketType", TypeIdValue(TypeId::LookupByName("ns3::TcpCubic")));

    TopologyState state;
    state.config = config;

    NodeContainer nodes;
    nodes.Create(5);
    state.source = nodes.Get(0);
    state.relayA = nodes.Get(1);
    state.relayB = nodes.Get(2);
    state.dest = nodes.Get(3);
    state.bgSink = nodes.Get(4);

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue(MbpsString(config.linkRateMbps)));
    p2p.SetChannelAttribute("Delay", StringValue(std::to_string(config.linkDelayMs) + "ms"));
    p2p.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize", StringValue(std::to_string(config.queuePackets) + "p"));

    auto installLink = [&](Ptr<Node> a, Ptr<Node> b, const char* subnet) {
        Ipv4AddressHelper helper;
        helper.SetBase(Ipv4Address(subnet), "255.255.255.0");
        auto devices = p2p.Install(NodeContainer(a, b));
        auto interfaces = helper.Assign(devices);
        return std::make_pair(devices, interfaces);
    };

    const auto l12 = installLink(state.source, state.relayA, "10.1.1.0");
    const auto l13 = installLink(state.source, state.relayB, "10.1.2.0");
    const auto l24 = installLink(state.relayA, state.dest, "10.1.3.0");
    const auto l34 = installLink(state.relayB, state.dest, "10.1.4.0");
    const auto l25 = installLink(state.relayA, state.bgSink, "10.1.5.0");

    state.destAddress = l24.second.GetAddress(1);
    state.bgSinkAddress = l25.second.GetAddress(1);
    state.pathA = {DynamicCast<PointToPointNetDevice>(l12.first.Get(0)),
                   l12.second.GetAddress(1),
                   static_cast<uint32_t>(state.source->GetObject<Ipv4>()->GetInterfaceForDevice(l12.first.Get(0)))};
    state.pathB = {DynamicCast<PointToPointNetDevice>(l13.first.Get(0)),
                   l13.second.GetAddress(1),
                   static_cast<uint32_t>(state.source->GetObject<Ipv4>()->GetInterfaceForDevice(l13.first.Get(0)))};

    auto srcRouting = GetStaticRouting(state.source);
    auto relayARouting = GetStaticRouting(state.relayA);
    auto relayBRouting = GetStaticRouting(state.relayB);
    auto destRouting = GetStaticRouting(state.dest);
    auto bgRouting = GetStaticRouting(state.bgSink);

    srcRouting->AddHostRouteTo(state.destAddress, state.pathA.nextHop, state.pathA.interfaceIndex);
    srcRouting->AddHostRouteTo(state.bgSinkAddress,
                               l12.second.GetAddress(1),
                               state.source->GetObject<Ipv4>()->GetInterfaceForDevice(l12.first.Get(0)));
    relayARouting->AddHostRouteTo(state.destAddress,
                                  l24.second.GetAddress(1),
                                  state.relayA->GetObject<Ipv4>()->GetInterfaceForDevice(l24.first.Get(0)));
    relayARouting->AddHostRouteTo(state.bgSinkAddress,
                                  l25.second.GetAddress(1),
                                  state.relayA->GetObject<Ipv4>()->GetInterfaceForDevice(l25.first.Get(0)));
    relayBRouting->AddHostRouteTo(state.destAddress,
                                  l34.second.GetAddress(1),
                                  state.relayB->GetObject<Ipv4>()->GetInterfaceForDevice(l34.first.Get(0)));
    destRouting->AddHostRouteTo(l12.second.GetAddress(0),
                                l24.second.GetAddress(0),
                                state.dest->GetObject<Ipv4>()->GetInterfaceForDevice(l24.first.Get(1)));
    destRouting->AddHostRouteTo(l13.second.GetAddress(0),
                                l34.second.GetAddress(0),
                                state.dest->GetObject<Ipv4>()->GetInterfaceForDevice(l34.first.Get(1)));
    bgRouting->AddHostRouteTo(l12.second.GetAddress(0),
                              l25.second.GetAddress(0),
                              state.bgSink->GetObject<Ipv4>()->GetInterfaceForDevice(l25.first.Get(1)));

    PacketSinkHelper primarySinkHelper("ns3::TcpSocketFactory",
                                       InetSocketAddress(Ipv4Address::GetAny(), state.primaryPort));
    auto primaryApps = primarySinkHelper.Install(state.dest);
    primaryApps.Start(Seconds(0.0));
    primaryApps.Stop(Seconds(config.simSeconds + 0.5));
    state.primarySink = DynamicCast<PacketSink>(primaryApps.Get(0));

    PacketSinkHelper bgSinkHelper("ns3::UdpSocketFactory",
                                  InetSocketAddress(Ipv4Address::GetAny(), state.bgPort));
    auto bgApps = bgSinkHelper.Install(state.bgSink);
    bgApps.Start(Seconds(0.0));
    bgApps.Stop(Seconds(config.simSeconds + 0.5));
    state.bgSinkApp = DynamicCast<PacketSink>(bgApps.Get(0));

    BulkSendHelper bulk("ns3::TcpSocketFactory", InetSocketAddress(state.destAddress, state.primaryPort));
    bulk.SetAttribute("SendSize", UintegerValue(1200));
    bulk.SetAttribute("MaxBytes", UintegerValue(0));
    auto bulkApps = bulk.Install(state.source);
    bulkApps.Start(Seconds(0.5));
    bulkApps.Stop(Seconds(config.simSeconds));
    state.primaryBulkApp = DynamicCast<BulkSendApplication>(bulkApps.Get(0));

    auto addBurst = [&](double start, double stop) {
        OnOffHelper bgOnOff("ns3::UdpSocketFactory", InetSocketAddress(state.bgSinkAddress, state.bgPort));
        bgOnOff.SetConstantRate(DataRate(MbpsString(config.backgroundRateMbps)), 1200);
        bgOnOff.SetAttribute("StartTime", TimeValue(Seconds(start)));
        bgOnOff.SetAttribute("StopTime", TimeValue(Seconds(stop)));
        bgOnOff.Install(state.source);
    };
    addBurst(2.0, 5.0);
    addBurst(7.0, 10.0);

    state.flowMonitor = state.flowHelper.InstallAll();
    state.timeline.open(config.timelineCsv, std::ios::out | std::ios::trunc);
    state.timeline << "time_s,selected_path,queue_a_norm,queue_b_norm,accum,memory_a,memory_b,burst_active\n";
    state.tcpTrace.cwnd.open(config.cwndCsv, std::ios::out | std::ios::trunc);
    state.tcpTrace.cwnd << "time_s,old_cwnd_bytes,new_cwnd_bytes\n";

    Simulator::Schedule(Seconds(config.controllerStartSeconds), &ControllerStep, &state);
    Simulator::Schedule(Seconds(0.7), &AttachTcpTrace, &state);
    Simulator::Schedule(Seconds(2.0), &RecordRxCheckpoint, &state, 2);
    Simulator::Schedule(Seconds(5.0), &RecordRxCheckpoint, &state, 5);
    Simulator::Schedule(Seconds(7.0), &RecordRxCheckpoint, &state, 7);
    Simulator::Schedule(Seconds(10.0), &RecordRxCheckpoint, &state, 10);
    Simulator::Schedule(Seconds(12.0), &RecordRxCheckpoint, &state, 12);
    Simulator::Stop(Seconds(config.simSeconds + 0.1));
    Simulator::Run();
    state.flowMonitor->CheckForLostPackets();
    WriteSummary(state);
    state.timeline.close();
    state.tcpTrace.cwnd.close();
    Simulator::Destroy();
    return 0;
}

} // namespace

int main(int argc, char* argv[])
{
    return MainBody(argc, argv);
}
