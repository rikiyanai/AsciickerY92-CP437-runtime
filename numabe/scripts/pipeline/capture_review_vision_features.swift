#!/usr/bin/env swift
// Local, network-free semantic feature distances for capture-review crops.

import Foundation
import Vision

struct InputRow: Codable { let annotation_id: String; let path: String }
struct Neighbor: Codable { let annotation_id: String; let distance: Float }
struct Classification: Codable { let identifier: String; let confidence: Float }
struct OutputRow: Codable {
    let annotation_id: String
    let neighbors: [Neighbor]
    let classifications: [Classification]
}
struct Output: Codable {
    let schema: String
    let backend: String
    let operating_system: String
    let nearest_neighbor_count: Int
    let observations: [OutputRow]
}

guard CommandLine.arguments.count == 4 else {
    FileHandle.standardError.write(Data("usage: capture_review_vision_features.swift INPUT.json OUTPUT.json K\n".utf8))
    exit(2)
}
let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
guard let neighborCount = Int(CommandLine.arguments[3]), neighborCount > 0 else {
    throw NSError(domain: "Y92Vision", code: 2, userInfo: [NSLocalizedDescriptionKey: "K must be positive"])
}
let inputs = try JSONDecoder().decode([InputRow].self, from: Data(contentsOf: inputURL))
var features: [(String, VNFeaturePrintObservation, [Classification])] = []
features.reserveCapacity(inputs.count)
for row in inputs.sorted(by: { $0.annotation_id < $1.annotation_id }) {
    let request = VNGenerateImageFeaturePrintRequest()
    let classificationRequest = VNClassifyImageRequest()
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: row.path), options: [:])
    try handler.perform([request, classificationRequest])
    guard let feature = request.results?.first as? VNFeaturePrintObservation else {
        throw NSError(domain: "Y92Vision", code: 3, userInfo: [NSLocalizedDescriptionKey: "no feature print for \(row.annotation_id)"])
    }
    let classifications = (classificationRequest.results ?? []).prefix(8).map {
        Classification(identifier: $0.identifier, confidence: $0.confidence)
    }
    features.append((row.annotation_id, feature, classifications))
}
var outputRows: [OutputRow] = []
for index in features.indices {
    var neighbors: [Neighbor] = []
    neighbors.reserveCapacity(max(0, features.count - 1))
    for otherIndex in features.indices where otherIndex != index {
        var distance: Float = 0
        try features[index].1.computeDistance(&distance, to: features[otherIndex].1)
        neighbors.append(Neighbor(annotation_id: features[otherIndex].0, distance: distance))
    }
    neighbors.sort {
        $0.distance == $1.distance ? $0.annotation_id < $1.annotation_id : $0.distance < $1.distance
    }
    outputRows.append(OutputRow(
        annotation_id: features[index].0,
        neighbors: Array(neighbors.prefix(neighborCount)),
        classifications: features[index].2
    ))
}
let output = Output(
    schema: "y92.capture_semantic_neighbors.v1",
    backend: "apple_vision_feature_print",
    operating_system: ProcessInfo.processInfo.operatingSystemVersionString,
    nearest_neighbor_count: neighborCount,
    observations: outputRows
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
var data = try encoder.encode(output)
data.append(0x0a)
try data.write(to: outputURL, options: .atomic)
