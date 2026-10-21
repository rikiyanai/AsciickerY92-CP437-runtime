// Generated binding for shared/runtime_data/runtime_data_contract.v1.json.
// Run `python3 scripts/asciid.py check-contract` after changing either
// file. Both current-source products and ASCIIID Classic include this header;
// product-local copies of the version bounds are forbidden.
#pragma once

namespace a3d_abi {

constexpr int kStoryIdVersion = 1;
constexpr int kItemRecordVersion = 3;
constexpr int kPlayerStartVersion = 4;
constexpr int kMaterialKeyVersion = 5;
constexpr int kMeshUidVersion = 6;
constexpr int kClassicMaxVersion = 4;
constexpr int kClassicWriteVersion = 4;
constexpr int kCurrentMaxVersion = 6;
constexpr int kCurrentWriteVersion = 6;

constexpr const char* kRejectClassicVersionUnsupported = "classic_version_unsupported";
constexpr const char* kRejectWorldVersionUnsupported = "world_version_unsupported";

} // namespace a3d_abi
