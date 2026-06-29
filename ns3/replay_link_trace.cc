#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-helper.h"
#include "ns3/ipv4-flow-classifier.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

using namespace ns3;

namespace {

struct EdgeRow {
  uint32_t src;
  uint32_t dst;
};

struct EventRow {
  uint32_t tick;
  std::string kind;
  uint32_t src;
  uint32_t dst;
  uint64_t bytes;
};

struct AggregateStats {
  uint64_t txBytes{0};
  uint64_t rxBytes{0};
  uint64_t txPackets{0};
  uint64_t rxPackets{0};
  uint64_t lostPackets{0};
  double delaySumSeconds{0.0};
};

struct ReplayConfig {
  std::string topologyCsv;
  std::string eventsCsv;
  std::string summaryJson;
  double linkRateMbps{10.0};
  double linkDelayMs{2.0};
  uint32_t tickMs{100};
  uint32_t queuePackets{100};
  uint32_t dataPacketSize{1200};
  uint32_t controlPacketSize{32};
  uint16_t dataPort{40000};
  uint16_t controlPort{40001};
};

std::vector<std::string> SplitCsvLine(const std::string& line) {
  std::vector<std::string> parts;
  std::stringstream ss(line);
  std::string part;
  while (std::getline(ss, part, ',')) {
    parts.push_back(part);
  }
  return parts;
}

std::vector<EdgeRow> ReadTopologyCsv(const std::string& path) {
  std::ifstream in(path);
  if (!in.is_open()) {
    NS_FATAL_ERROR("unable to open topology csv: " << path);
  }
  std::vector<EdgeRow> edges;
  std::string line;
  bool first = true;
  while (std::getline(in, line)) {
    if (line.empty()) {
      continue;
    }
    if (first) {
      first = false;
      continue;
    }
    auto cols = SplitCsvLine(line);
    if (cols.size() < 2) {
      continue;
    }
    edges.push_back({static_cast<uint32_t>(std::stoul(cols[0])), static_cast<uint32_t>(std::stoul(cols[1]))});
  }
  return edges;
}

std::vector<EventRow> ReadEventsCsv(const std::string& path) {
  std::ifstream in(path);
  if (!in.is_open()) {
    NS_FATAL_ERROR("unable to open events csv: " << path);
  }
  std::vector<EventRow> rows;
  std::string line;
  bool first = true;
  while (std::getline(in, line)) {
    if (line.empty()) {
      continue;
    }
    if (first) {
      first = false;
      continue;
    }
    auto cols = SplitCsvLine(line);
    if (cols.size() < 5) {
      continue;
    }
    rows.push_back(
        {static_cast<uint32_t>(std::stoul(cols[0])), cols[1], static_cast<uint32_t>(std::stoul(cols[2])),
         static_cast<uint32_t>(std::stoul(cols[3])), static_cast<uint64_t>(std::stoull(cols[4]))});
  }
  return rows;
}

void SendBurstPacket(Ptr<Socket> socket,
                     Address address,
                     uint32_t payloadSize,
                     uint64_t remainingBytes,
                     Time interval) {
  const uint32_t sendSize = static_cast<uint32_t>(std::min<uint64_t>(payloadSize, remainingBytes));
  socket->SendTo(Create<Packet>(sendSize), 0, address);
  if (remainingBytes > sendSize) {
    Simulator::Schedule(interval, &SendBurstPacket, socket, address, payloadSize, remainingBytes - sendSize, interval);
  }
}

void WriteSummaryJson(const std::string& path,
                      const ReplayConfig& config,
                      double totalSimSeconds,
                      const AggregateStats& dataAgg,
                      const AggregateStats& controlAgg) {
  std::ofstream out(path);
  out << std::fixed << std::setprecision(6);
  out << "{\n";
  out << "  \"tick_ms\": " << config.tickMs << ",\n";
  out << "  \"link_rate_mbps\": " << config.linkRateMbps << ",\n";
  out << "  \"link_delay_ms\": " << config.linkDelayMs << ",\n";
  out << "  \"queue_packets\": " << config.queuePackets << ",\n";
  out << "  \"total_simulation_seconds\": " << totalSimSeconds << ",\n";
  auto writeBlock = [&](const char* name, const AggregateStats& agg, bool trailingComma) {
    out << "  \"" << name << "\": {\n";
    out << "    \"tx_bytes\": " << agg.txBytes << ",\n";
    out << "    \"rx_bytes\": " << agg.rxBytes << ",\n";
    out << "    \"tx_packets\": " << agg.txPackets << ",\n";
    out << "    \"rx_packets\": " << agg.rxPackets << ",\n";
    out << "    \"lost_packets\": " << agg.lostPackets << ",\n";
    out << "    \"delivery_ratio\": " << (agg.txBytes == 0 ? 0.0 : static_cast<double>(agg.rxBytes) / static_cast<double>(agg.txBytes))
        << ",\n";
    out << "    \"mean_delay_ms\": "
        << (agg.rxPackets == 0 ? 0.0 : (agg.delaySumSeconds * 1000.0) / static_cast<double>(agg.rxPackets)) << "\n";
    out << "  }" << (trailingComma ? "," : "") << "\n";
  };
  writeBlock("data", dataAgg, true);
  writeBlock("control", controlAgg, false);
  out << "}\n";
}

}  // namespace

int main(int argc, char* argv[]) {
  ReplayConfig config;
  CommandLine cmd(__FILE__);
  cmd.AddValue("topologyCsv", "Undirected topology CSV with src,dst", config.topologyCsv);
  cmd.AddValue("eventsCsv", "Link-level replay events CSV", config.eventsCsv);
  cmd.AddValue("summaryJson", "Output summary JSON path", config.summaryJson);
  cmd.AddValue("linkRateMbps", "Point-to-point link rate in Mbps", config.linkRateMbps);
  cmd.AddValue("linkDelayMs", "Point-to-point propagation delay in ms", config.linkDelayMs);
  cmd.AddValue("tickMs", "Replay tick duration in ms", config.tickMs);
  cmd.AddValue("queuePackets", "Device queue depth in packets", config.queuePackets);
  cmd.AddValue("dataPacketSize", "Data packet payload size in bytes", config.dataPacketSize);
  cmd.AddValue("controlPacketSize", "Control packet payload size in bytes", config.controlPacketSize);
  cmd.Parse(argc, argv);

  if (config.topologyCsv.empty() || config.eventsCsv.empty() || config.summaryJson.empty()) {
    NS_FATAL_ERROR("topologyCsv, eventsCsv, and summaryJson are required");
  }

  const auto edges = ReadTopologyCsv(config.topologyCsv);
  const auto events = ReadEventsCsv(config.eventsCsv);
  uint32_t maxNodeId = 0;
  uint32_t maxTick = 0;
  for (const auto& edge : edges) {
    maxNodeId = std::max(maxNodeId, std::max(edge.src, edge.dst));
  }
  for (const auto& event : events) {
    maxNodeId = std::max(maxNodeId, std::max(event.src, event.dst));
    maxTick = std::max(maxTick, event.tick);
  }
  if (maxNodeId == 0) {
    NS_FATAL_ERROR("no nodes found in topology/events");
  }

  NodeContainer nodes;
  nodes.Create(maxNodeId);

  InternetStackHelper internet;
  internet.Install(nodes);

  PointToPointHelper p2p;
  std::ostringstream rate;
  rate << config.linkRateMbps << "Mbps";
  std::ostringstream delay;
  delay << config.linkDelayMs << "ms";
  p2p.SetDeviceAttribute("DataRate", StringValue(rate.str()));
  p2p.SetChannelAttribute("Delay", StringValue(delay.str()));
  p2p.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize", StringValue(std::to_string(config.queuePackets) + "p"));

  Ipv4AddressHelper address;
  uint32_t subnetIndex = 1;
  std::map<std::pair<uint32_t, uint32_t>, Ipv4Address> directedAddresses;

  for (const auto& edge : edges) {
    NodeContainer pair(nodes.Get(edge.src - 1), nodes.Get(edge.dst - 1));
    NetDeviceContainer devices = p2p.Install(pair);
    std::ostringstream subnet;
    subnet << "10." << ((subnetIndex / 256) % 256) << "." << (subnetIndex % 256) << ".0";
    address.SetBase(Ipv4Address(subnet.str().c_str()), "255.255.255.0");
    Ipv4InterfaceContainer ifaces = address.Assign(devices);
    directedAddresses[{edge.src, edge.dst}] = ifaces.GetAddress(1);
    directedAddresses[{edge.dst, edge.src}] = ifaces.GetAddress(0);
    ++subnetIndex;
  }

  Ipv4GlobalRoutingHelper::PopulateRoutingTables();

  PacketSinkHelper dataSinkHelper("ns3::UdpSocketFactory",
                                  InetSocketAddress(Ipv4Address::GetAny(), config.dataPort));
  PacketSinkHelper controlSinkHelper("ns3::UdpSocketFactory",
                                     InetSocketAddress(Ipv4Address::GetAny(), config.controlPort));
  ApplicationContainer sinkApps;
  for (uint32_t i = 0; i < maxNodeId; ++i) {
    sinkApps.Add(dataSinkHelper.Install(nodes.Get(i)));
    sinkApps.Add(controlSinkHelper.Install(nodes.Get(i)));
  }
  sinkApps.Start(Seconds(0.0));
  sinkApps.Stop(Seconds(((maxTick + 2) * config.tickMs) / 1000.0 + 1.0));

  std::vector<Ptr<Socket>> dataSockets(maxNodeId + 1);
  std::vector<Ptr<Socket>> controlSockets(maxNodeId + 1);
  for (uint32_t nodeId = 1; nodeId <= maxNodeId; ++nodeId) {
    dataSockets[nodeId] = Socket::CreateSocket(nodes.Get(nodeId - 1), UdpSocketFactory::GetTypeId());
    controlSockets[nodeId] = Socket::CreateSocket(nodes.Get(nodeId - 1), UdpSocketFactory::GetTypeId());
  }

  FlowMonitorHelper flowHelper;
  Ptr<FlowMonitor> flowMonitor = flowHelper.InstallAll();

  uint64_t eventIndex = 0;
  for (const auto& event : events) {
    if (event.bytes == 0) {
      continue;
    }
    auto addressIt = directedAddresses.find({event.src, event.dst});
    if (addressIt == directedAddresses.end()) {
      continue;
    }
    const bool isControl = (event.kind == "control");
    Ptr<Socket> socket = isControl ? controlSockets[event.src] : dataSockets[event.src];
    const uint32_t packetSize = isControl ? config.controlPacketSize : config.dataPacketSize;
    const uint16_t port = isControl ? config.controlPort : config.dataPort;
    const Address dest = InetSocketAddress(addressIt->second, port);
    const uint64_t packetCount =
        std::max<uint64_t>(1, (event.bytes + static_cast<uint64_t>(packetSize) - 1) / static_cast<uint64_t>(packetSize));
    const Time duration = MilliSeconds(config.tickMs);
    const Time interval =
        packetCount <= 1 ? duration : NanoSeconds(std::max<int64_t>(1, duration.GetNanoSeconds() / static_cast<int64_t>(packetCount)));
    const Time start = MilliSeconds(static_cast<uint64_t>(event.tick) * config.tickMs) + MicroSeconds(10 + (eventIndex % 1000));
    Simulator::Schedule(start, &SendBurstPacket, socket, dest, packetSize, event.bytes, interval);
    ++eventIndex;
  }

  const double totalSimSeconds = ((maxTick + 2) * config.tickMs) / 1000.0 + 1.0;
  Simulator::Stop(Seconds(totalSimSeconds));
  Simulator::Run();

  flowMonitor->CheckForLostPackets();
  Ptr<Ipv4FlowClassifier> classifier = DynamicCast<Ipv4FlowClassifier>(flowHelper.GetClassifier());
  AggregateStats dataAgg;
  AggregateStats controlAgg;
  for (const auto& item : flowMonitor->GetFlowStats()) {
    const auto tuple = classifier->FindFlow(item.first);
    AggregateStats* agg = nullptr;
    if (tuple.destinationPort == config.dataPort) {
      agg = &dataAgg;
    } else if (tuple.destinationPort == config.controlPort) {
      agg = &controlAgg;
    } else {
      continue;
    }
    agg->txBytes += item.second.txBytes;
    agg->rxBytes += item.second.rxBytes;
    agg->txPackets += item.second.txPackets;
    agg->rxPackets += item.second.rxPackets;
    agg->lostPackets += item.second.lostPackets;
    agg->delaySumSeconds += item.second.delaySum.GetSeconds();
  }

  WriteSummaryJson(config.summaryJson, config, totalSimSeconds, dataAgg, controlAgg);
  Simulator::Destroy();
  return 0;
}
