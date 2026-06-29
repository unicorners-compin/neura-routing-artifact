#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-helper.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"

#include <algorithm>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <map>
#include <numeric>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

using namespace ns3;

namespace {

struct Config {
  std::string method{"neura"};
  std::string scenario{"hotspot"};
  std::string summaryJson;
  std::string timelineCsv;
  uint32_t nodes{24};
  uint32_t seed{51};
  uint32_t targetFlows{10};
  double edgeProb{0.18};
  double linkRateMbps{10.0};
  double linkDelayMs{2.0};
  uint32_t queuePackets{20};
  double sampleMs{100.0};
  double simSeconds{12.0};
  double primaryRateMbps{1.2};
  double backgroundRateMbps{12.0};
  double threshold{0.20};
  double triggerMargin{0.06};
  double hysteresis{0.02};
  double guard{0.08};
  double memoryDecay{0.90};
  double memoryGain{0.30};
  double accumDecay{0.12};
  double accumGain{0.70};
  double refractoryMs{700.0};
  double minSwitchMs{200.0};
  double ospfPeriodMs{500.0};
};

struct LinkInfo {
  uint32_t src{0};
  uint32_t dst{0};
  Ptr<PointToPointNetDevice> srcDevice;
  Ipv4Address dstAddress;
  uint32_t interfaceIndex{0};
};

struct FlowState {
  uint32_t id{0};
  uint32_t src{0};
  uint32_t dst{0};
  uint32_t primaryHop{0};
  uint32_t alternateHop{0};
  uint16_t port{0};
  bool onAlternate{false};
  double memoryPrimary{0.0};
  double memoryAlternate{0.0};
  double accum{0.0};
  double lastSwitch{-1e9};
  double nextAllowed{-1e9};
  uint32_t routeChanges{0};
  uint32_t tailSwitches{0};
  Ptr<PacketSink> sink;
};

struct Controller {
  Config config;
  NodeContainer nodes;
  std::vector<std::pair<uint32_t, uint32_t>> undirectedEdges;
  std::map<std::pair<uint32_t, uint32_t>, LinkInfo> directedLinks;
  std::vector<std::vector<uint32_t>> adj;
  std::vector<Ipv4Address> nodeAddress;
  std::vector<FlowState> flows;
  std::vector<Ptr<Socket>> controlSockets;
  FlowMonitorHelper flowHelper;
  Ptr<FlowMonitor> flowMonitor;
  std::ofstream timeline;
  uint64_t logicalControlBytes{0};
  uint32_t controlPackets{0};
  uint32_t routeChanges{0};
  uint32_t tailSwitches{0};
  uint32_t ospfEmissions{0};
  uint16_t controlPort{49000};
  uint16_t backgroundPort{48000};
};

std::string MbpsString(double value) {
  std::ostringstream out;
  out << std::fixed << std::setprecision(3) << value << "Mbps";
  return out.str();
}

double Clamp01(double value) {
  return std::max(0.0, std::min(1.0, value));
}

bool BurstActive(const std::string& scenario, double now) {
  if (scenario == "hotspot") {
    return now >= 2.0 && now < 5.0;
  }
  return (now >= 2.0 && now < 5.0) || (now >= 7.0 && now < 10.0);
}

std::vector<uint32_t> ShortestPath(const std::vector<std::vector<uint32_t>>& adj,
                                   uint32_t src,
                                   uint32_t dst,
                                   std::pair<uint32_t, uint32_t> banned = {0, 0}) {
  std::vector<int> parent(adj.size(), -1);
  std::deque<uint32_t> q;
  parent[src] = static_cast<int>(src);
  q.push_back(src);
  while (!q.empty()) {
    uint32_t u = q.front();
    q.pop_front();
    if (u == dst) {
      break;
    }
    for (uint32_t v : adj[u]) {
      if ((u == banned.first && v == banned.second) || (u == banned.second && v == banned.first)) {
        continue;
      }
      if (parent[v] >= 0) {
        continue;
      }
      parent[v] = static_cast<int>(u);
      q.push_back(v);
    }
  }
  if (parent[dst] < 0) {
    return {};
  }
  std::vector<uint32_t> path;
  for (uint32_t cur = dst; cur != src; cur = static_cast<uint32_t>(parent[cur])) {
    path.push_back(cur);
  }
  path.push_back(src);
  std::reverse(path.begin(), path.end());
  return path;
}

std::vector<std::pair<uint32_t, uint32_t>> MakeConnectedEr(uint32_t n, double p, uint32_t seed) {
  std::mt19937 rng(seed);
  std::uniform_real_distribution<double> uni(0.0, 1.0);
  std::set<std::pair<uint32_t, uint32_t>> edges;
  for (uint32_t i = 0; i + 1 < n; ++i) {
    edges.insert({i, i + 1});
  }
  for (uint32_t i = 0; i < n; ++i) {
    for (uint32_t j = i + 2; j < n; ++j) {
      if (uni(rng) < p) {
        edges.insert({i, j});
      }
    }
  }
  return {edges.begin(), edges.end()};
}

Ptr<Ipv4StaticRouting> StaticRouting(Ptr<Node> node) {
  Ipv4StaticRoutingHelper helper;
  return helper.GetStaticRouting(node->GetObject<Ipv4>());
}

double QueueNorm(const Controller* ctl, uint32_t src, uint32_t hop) {
  auto it = ctl->directedLinks.find({src, hop});
  if (it == ctl->directedLinks.end() || it->second.srcDevice == nullptr || ctl->config.queuePackets == 0) {
    return 0.0;
  }
  auto queue = it->second.srcDevice->GetQueue();
  if (queue == nullptr) {
    return 0.0;
  }
  return Clamp01(static_cast<double>(queue->GetNPackets()) / static_cast<double>(ctl->config.queuePackets));
}

void EmitControl(Controller* ctl, uint32_t src, bool global) {
  std::vector<uint32_t> targets;
  if (global) {
    for (uint32_t u = 0; u < ctl->adj.size(); ++u) {
      for (uint32_t v : ctl->adj[u]) {
        targets.push_back((u << 16) | v);
      }
    }
  } else {
    for (uint32_t v : ctl->adj[src]) {
      targets.push_back((src << 16) | v);
    }
  }
  for (uint32_t packed : targets) {
    uint32_t u = packed >> 16;
    uint32_t v = packed & 0xffffu;
    auto link = ctl->directedLinks.find({u, v});
    if (link == ctl->directedLinks.end() || ctl->controlSockets[u] == nullptr) {
      continue;
    }
    ctl->controlSockets[u]->SendTo(Create<Packet>(32), 0, InetSocketAddress(link->second.dstAddress, ctl->controlPort));
    ctl->logicalControlBytes += 32;
    ctl->controlPackets += 1;
  }
}

void ReplaceRoute(Controller* ctl, FlowState& flow, bool useAlternate, double now) {
  if (flow.onAlternate == useAlternate || now < flow.nextAllowed) {
    return;
  }
  auto routing = StaticRouting(ctl->nodes.Get(flow.src));
  Ipv4Address dstAddr = ctl->nodeAddress[flow.dst];
  for (int i = static_cast<int>(routing->GetNRoutes()) - 1; i >= 0; --i) {
    auto route = routing->GetRoute(static_cast<uint32_t>(i));
    if (route.IsHost() && route.GetDest() == dstAddr) {
      routing->RemoveRoute(static_cast<uint32_t>(i));
    }
  }
  uint32_t hop = useAlternate ? flow.alternateHop : flow.primaryHop;
  const auto& link = ctl->directedLinks[{flow.src, hop}];
  routing->AddHostRouteTo(dstAddr, link.dstAddress, link.interfaceIndex);
  flow.onAlternate = useAlternate;
  flow.routeChanges += 1;
  ctl->routeChanges += 1;
  if (!BurstActive(ctl->config.scenario, now)) {
    flow.tailSwitches += 1;
    ctl->tailSwitches += 1;
  }
  flow.lastSwitch = now;
  flow.nextAllowed = now + (ctl->config.method == "neura" ? ctl->config.refractoryMs : ctl->config.minSwitchMs) / 1000.0;
  EmitControl(ctl, flow.src, false);
}

void ControllerStep(Controller* ctl) {
  const double now = Simulator::Now().GetSeconds();
  const bool burst = BurstActive(ctl->config.scenario, now);
  if (ctl->config.method == "ospf_te") {
    EmitControl(ctl, 0, true);
    ctl->ospfEmissions += 1;
  }

  double meanCurrentQ = 0.0;
  double meanAltQ = 0.0;
  for (auto& flow : ctl->flows) {
    const uint32_t currentHop = flow.onAlternate ? flow.alternateHop : flow.primaryHop;
    const uint32_t altHop = flow.onAlternate ? flow.primaryHop : flow.alternateHop;
    const double qCurrent = QueueNorm(ctl, flow.src, currentHop);
    const double qAlt = QueueNorm(ctl, flow.src, altHop);
    meanCurrentQ += qCurrent;
    meanAltQ += qAlt;

    flow.memoryPrimary = flow.memoryPrimary * ctl->config.memoryDecay + ctl->config.memoryGain * QueueNorm(ctl, flow.src, flow.primaryHop);
    flow.memoryAlternate = flow.memoryAlternate * ctl->config.memoryDecay + ctl->config.memoryGain * QueueNorm(ctl, flow.src, flow.alternateHop);

    const double currentMemory = flow.onAlternate ? flow.memoryAlternate : flow.memoryPrimary;
    const double altMemory = flow.onAlternate ? flow.memoryPrimary : flow.memoryAlternate;
    const double baseCurrent = flow.onAlternate ? 0.05 : 0.0;
    const double baseAlt = flow.onAlternate ? 0.0 : 0.05;

    if (ctl->config.method == "neura") {
      const double diff = (baseCurrent + qCurrent + 0.35 * currentMemory) - (baseAlt + qAlt + 0.35 * altMemory);
      flow.accum = std::max(0.0, flow.accum * (1.0 - ctl->config.accumDecay) + ctl->config.accumGain * diff);
      const double effCurrent = baseCurrent + qCurrent + 0.45 * currentMemory;
      const double effAlt = baseAlt + qAlt + 0.45 * altMemory;
      if (flow.accum >= ctl->config.threshold && effAlt + ctl->config.guard < effCurrent) {
        ReplaceRoute(ctl, flow, !flow.onAlternate, now);
        flow.accum = 0.0;
      }
    } else if (ctl->config.method == "triggered_te") {
      const double currentCost = baseCurrent + qCurrent;
      const double altCost = baseAlt + qAlt;
      const double margin = BurstActive(ctl->config.scenario, now) ? ctl->config.triggerMargin : ctl->config.hysteresis;
      if (altCost + margin < currentCost) {
        ReplaceRoute(ctl, flow, !flow.onAlternate, now);
      }
    } else {
      const bool chooseAlt = (0.05 + QueueNorm(ctl, flow.src, flow.alternateHop)) < QueueNorm(ctl, flow.src, flow.primaryHop);
      ReplaceRoute(ctl, flow, chooseAlt, now);
    }
  }
  if (!ctl->flows.empty()) {
    meanCurrentQ /= static_cast<double>(ctl->flows.size());
    meanAltQ /= static_cast<double>(ctl->flows.size());
  }
  ctl->timeline << std::fixed << std::setprecision(6) << now << "," << (burst ? 1 : 0) << ","
                << meanCurrentQ << "," << meanAltQ << "," << ctl->routeChanges << ","
                << ctl->tailSwitches << "," << ctl->logicalControlBytes << "\n";

  const double delay = ctl->config.sampleMs / 1000.0;
  if (now + delay <= ctl->config.simSeconds + 1e-9) {
    Simulator::Schedule(Seconds(delay), &ControllerStep, ctl);
  }
}

void WriteSummary(Controller& ctl) {
  ctl.flowMonitor->CheckForLostPackets();
  auto classifier = DynamicCast<Ipv4FlowClassifier>(ctl.flowHelper.GetClassifier());
  uint64_t primaryTx = 0, primaryRx = 0, primaryRxPackets = 0;
  uint64_t backgroundTx = 0, backgroundRx = 0;
  uint64_t controlTx = 0, controlRx = 0;
  double primaryDelay = 0.0;
  const auto stats = ctl.flowMonitor->GetFlowStats();
  for (const auto& item : stats) {
    auto tuple = classifier->FindFlow(item.first);
    if (tuple.destinationPort >= 50000 && tuple.destinationPort < 60000) {
      primaryTx += item.second.txBytes;
      primaryRx += item.second.rxBytes;
      primaryRxPackets += item.second.rxPackets;
      primaryDelay += item.second.delaySum.GetSeconds();
    } else if (tuple.destinationPort == ctl.backgroundPort) {
      backgroundTx += item.second.txBytes;
      backgroundRx += item.second.rxBytes;
    } else if (tuple.destinationPort == ctl.controlPort) {
      controlTx += item.second.txBytes;
      controlRx += item.second.rxBytes;
    }
  }
  std::ofstream out(ctl.config.summaryJson);
  out << std::fixed << std::setprecision(6);
  out << "{\n";
  out << "  \"method\": \"" << ctl.config.method << "\",\n";
  out << "  \"scenario\": \"" << ctl.config.scenario << "\",\n";
  out << "  \"nodes\": " << ctl.config.nodes << ",\n";
  out << "  \"seed\": " << ctl.config.seed << ",\n";
  out << "  \"flows\": " << ctl.flows.size() << ",\n";
  out << "  \"edges\": " << ctl.undirectedEdges.size() << ",\n";
  out << "  \"primary_delivery_ratio\": " << (primaryTx == 0 ? 0.0 : static_cast<double>(primaryRx) / primaryTx) << ",\n";
  out << "  \"primary_mean_delay_ms\": " << (primaryRxPackets == 0 ? 0.0 : 1000.0 * primaryDelay / primaryRxPackets) << ",\n";
  out << "  \"primary_goodput_mbps\": " << (static_cast<double>(primaryRx) * 8.0 / (ctl.config.simSeconds * 1'000'000.0)) << ",\n";
  out << "  \"background_delivery_ratio\": " << (backgroundTx == 0 ? 0.0 : static_cast<double>(backgroundRx) / backgroundTx) << ",\n";
  out << "  \"route_changes\": " << ctl.routeChanges << ",\n";
  out << "  \"tail_switches\": " << ctl.tailSwitches << ",\n";
  out << "  \"logical_control_bytes\": " << ctl.logicalControlBytes << ",\n";
  out << "  \"ns3_control_tx_bytes\": " << controlTx << ",\n";
  out << "  \"ns3_control_rx_bytes\": " << controlRx << ",\n";
  out << "  \"control_packets\": " << ctl.controlPackets << ",\n";
  out << "  \"ospf_emissions\": " << ctl.ospfEmissions << "\n";
  out << "}\n";
}

int MainBody(int argc, char* argv[]) {
  Config config;
  CommandLine cmd(__FILE__);
  cmd.AddValue("method", "neura, triggered_te, or ospf_te", config.method);
  cmd.AddValue("scenario", "hotspot or repeated", config.scenario);
  cmd.AddValue("summaryJson", "Output summary JSON path", config.summaryJson);
  cmd.AddValue("timelineCsv", "Output timeline CSV path", config.timelineCsv);
  cmd.AddValue("nodes", "Number of ER nodes", config.nodes);
  cmd.AddValue("seed", "Random seed", config.seed);
  cmd.AddValue("targetFlows", "Target number of primary flows", config.targetFlows);
  cmd.AddValue("edgeProb", "ER extra edge probability", config.edgeProb);
  cmd.AddValue("linkRateMbps", "Link rate in Mbps", config.linkRateMbps);
  cmd.AddValue("queuePackets", "DropTail queue depth", config.queuePackets);
  cmd.AddValue("sampleMs", "Controller sample interval", config.sampleMs);
  cmd.AddValue("simSeconds", "Simulation duration", config.simSeconds);
  cmd.AddValue("primaryRateMbps", "Primary UDP flow rate", config.primaryRateMbps);
  cmd.AddValue("backgroundRateMbps", "Background UDP burst rate per stressed source", config.backgroundRateMbps);
  cmd.Parse(argc, argv);
  if (config.summaryJson.empty() || config.timelineCsv.empty()) {
    NS_FATAL_ERROR("summaryJson and timelineCsv are required");
  }

  Controller ctl;
  ctl.config = config;
  ctl.undirectedEdges = MakeConnectedEr(config.nodes, config.edgeProb, config.seed);
  ctl.adj.assign(config.nodes, {});
  for (auto [u, v] : ctl.undirectedEdges) {
    ctl.adj[u].push_back(v);
    ctl.adj[v].push_back(u);
  }
  for (auto& nbrs : ctl.adj) {
    std::sort(nbrs.begin(), nbrs.end());
  }

  ctl.nodes.Create(config.nodes);
  InternetStackHelper internet;
  internet.Install(ctl.nodes);
  PointToPointHelper p2p;
  p2p.SetDeviceAttribute("DataRate", StringValue(MbpsString(config.linkRateMbps)));
  p2p.SetChannelAttribute("Delay", StringValue(std::to_string(config.linkDelayMs) + "ms"));
  p2p.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize", StringValue(std::to_string(config.queuePackets) + "p"));
  Ipv4AddressHelper addr;
  ctl.nodeAddress.assign(config.nodes, Ipv4Address("0.0.0.0"));
  uint32_t subnet = 1;
  for (auto [u, v] : ctl.undirectedEdges) {
    auto devices = p2p.Install(NodeContainer(ctl.nodes.Get(u), ctl.nodes.Get(v)));
    std::ostringstream net;
    net << "10." << ((subnet / 256) % 256) << "." << (subnet % 256) << ".0";
    addr.SetBase(Ipv4Address(net.str().c_str()), "255.255.255.0");
    auto ifaces = addr.Assign(devices);
    auto devU = DynamicCast<PointToPointNetDevice>(devices.Get(0));
    auto devV = DynamicCast<PointToPointNetDevice>(devices.Get(1));
    ctl.directedLinks[{u, v}] = {u, v, devU, ifaces.GetAddress(1), static_cast<uint32_t>(ctl.nodes.Get(u)->GetObject<Ipv4>()->GetInterfaceForDevice(devices.Get(0)))};
    ctl.directedLinks[{v, u}] = {v, u, devV, ifaces.GetAddress(0), static_cast<uint32_t>(ctl.nodes.Get(v)->GetObject<Ipv4>()->GetInterfaceForDevice(devices.Get(1)))};
    if (ctl.nodeAddress[u] == Ipv4Address("0.0.0.0")) {
      ctl.nodeAddress[u] = ifaces.GetAddress(0);
    }
    if (ctl.nodeAddress[v] == Ipv4Address("0.0.0.0")) {
      ctl.nodeAddress[v] = ifaces.GetAddress(1);
    }
    ++subnet;
  }
  Ipv4GlobalRoutingHelper::PopulateRoutingTables();

  PacketSinkHelper controlSink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), ctl.controlPort));
  PacketSinkHelper bgSink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), ctl.backgroundPort));
  ApplicationContainer sinkApps;
  for (uint32_t i = 0; i < config.nodes; ++i) {
    sinkApps.Add(controlSink.Install(ctl.nodes.Get(i)));
    sinkApps.Add(bgSink.Install(ctl.nodes.Get(i)));
  }
  sinkApps.Start(Seconds(0.0));
  sinkApps.Stop(Seconds(config.simSeconds + 0.5));
  ctl.controlSockets.resize(config.nodes);
  for (uint32_t i = 0; i < config.nodes; ++i) {
    ctl.controlSockets[i] = Socket::CreateSocket(ctl.nodes.Get(i), UdpSocketFactory::GetTypeId());
  }

  std::mt19937 rng(config.seed + 17);
  std::vector<uint32_t> order(config.nodes);
  std::iota(order.begin(), order.end(), 0);
  std::shuffle(order.begin(), order.end(), rng);
  uint16_t port = 50000;
  uint32_t hotspot = order[0];
  for (uint32_t src : order) {
    if (ctl.flows.size() >= config.targetFlows || ctl.adj[src].size() < 2) {
      continue;
    }
    for (uint32_t dstCandidate : order) {
      if (src == dstCandidate || dstCandidate == hotspot) {
        continue;
      }
      auto path = ShortestPath(ctl.adj, src, dstCandidate);
      if (path.size() < 3) {
        continue;
      }
      auto alt = ShortestPath(ctl.adj, src, dstCandidate, {src, path[1]});
      if (alt.size() < 3 || alt[1] == path[1]) {
        continue;
      }
      FlowState flow;
      flow.id = static_cast<uint32_t>(ctl.flows.size());
      flow.src = src;
      flow.dst = dstCandidate;
      flow.primaryHop = path[1];
      flow.alternateHop = alt[1];
      flow.port = port++;
      ctl.flows.push_back(flow);
      break;
    }
  }
  if (ctl.flows.empty()) {
    NS_FATAL_ERROR("no usable two-path flows found");
  }

  ApplicationContainer trafficApps;
  for (auto& flow : ctl.flows) {
    PacketSinkHelper primarySink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), flow.port));
    auto apps = primarySink.Install(ctl.nodes.Get(flow.dst));
    apps.Start(Seconds(0.0));
    apps.Stop(Seconds(config.simSeconds + 0.5));
    flow.sink = DynamicCast<PacketSink>(apps.Get(0));

    const auto& link = ctl.directedLinks[{flow.src, flow.primaryHop}];
    StaticRouting(ctl.nodes.Get(flow.src))->AddHostRouteTo(ctl.nodeAddress[flow.dst], link.dstAddress, link.interfaceIndex);

    OnOffHelper primary("ns3::UdpSocketFactory", InetSocketAddress(ctl.nodeAddress[flow.dst], flow.port));
    primary.SetConstantRate(DataRate(MbpsString(config.primaryRateMbps)), 1200);
    primary.SetAttribute("StartTime", TimeValue(Seconds(0.5)));
    primary.SetAttribute("StopTime", TimeValue(Seconds(config.simSeconds)));
    trafficApps.Add(primary.Install(ctl.nodes.Get(flow.src)));

    OnOffHelper bg("ns3::UdpSocketFactory", InetSocketAddress(ctl.nodeAddress[flow.primaryHop], ctl.backgroundPort));
    bg.SetConstantRate(DataRate(MbpsString(config.backgroundRateMbps)), 1200);
    bg.SetAttribute("StartTime", TimeValue(Seconds(2.0)));
    bg.SetAttribute("StopTime", TimeValue(Seconds(5.0)));
    trafficApps.Add(bg.Install(ctl.nodes.Get(flow.src)));
    if (config.scenario == "repeated") {
      OnOffHelper bg2("ns3::UdpSocketFactory", InetSocketAddress(ctl.nodeAddress[flow.primaryHop], ctl.backgroundPort));
      bg2.SetConstantRate(DataRate(MbpsString(config.backgroundRateMbps)), 1200);
      bg2.SetAttribute("StartTime", TimeValue(Seconds(7.0)));
      bg2.SetAttribute("StopTime", TimeValue(Seconds(10.0)));
      trafficApps.Add(bg2.Install(ctl.nodes.Get(flow.src)));
    }
  }

  ctl.flowMonitor = ctl.flowHelper.InstallAll();
  ctl.timeline.open(config.timelineCsv, std::ios::out | std::ios::trunc);
  ctl.timeline << "time_s,burst_active,mean_current_queue,mean_alt_queue,route_changes,tail_switches,logical_control_bytes\n";
  Simulator::Schedule(Seconds(1.0), &ControllerStep, &ctl);
  Simulator::Stop(Seconds(config.simSeconds + 0.2));
  Simulator::Run();
  WriteSummary(ctl);
  ctl.timeline.close();
  Simulator::Destroy();
  return 0;
}

}  // namespace

int main(int argc, char* argv[]) {
  return MainBody(argc, argv);
}
