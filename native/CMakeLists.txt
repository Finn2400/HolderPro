cmake_minimum_required(VERSION 3.24)

project(HolderProNative VERSION 0.1.0 LANGUAGES C CXX)

set(HOLDERPRO_VERSION "0.1.0a1" CACHE STRING
    "HolderPro product version reported by the native engine")
set(HOLDERPRO_ADAPTER_VERSION "1" CACHE STRING
    "Version of the HolderPro-to-PrusaSlicer adapter interface")
set(HOLDERPRO_BUILD_ID "local" CACHE STRING
    "Reproducible release/build identifier reported by the native engine")
set(HOLDERPRO_RUNTIME_DEPENDENCY_DIR "" CACHE PATH
    "Directory containing non-system runtime libraries to stage with the engine")
if(WIN32 AND NOT HOLDERPRO_RUNTIME_DEPENDENCY_DIR AND CMAKE_PREFIX_PATH)
    set(_holderpro_prefixes ${CMAKE_PREFIX_PATH})
    list(GET _holderpro_prefixes 0 _holderpro_primary_prefix)
    set(HOLDERPRO_RUNTIME_DEPENDENCY_DIR
        "${_holderpro_primary_prefix}/bin" CACHE PATH
        "Directory containing non-system runtime libraries to stage with the engine"
        FORCE)
endif()
option(HOLDERPRO_ENABLE_LTO
    "Enable interprocedural optimization in Release builds when supported" ON)
option(HOLDERPRO_ENABLE_DEAD_CODE_ELIMINATION
    "Compile/link Release builds so unused native code can be discarded" ON)

set(PRUSASLICER_SOURCE_DIR "" CACHE PATH
    "Path to a complete PrusaSlicer version_2.9.6 source checkout")

set(_prusa_commit "b028299c770b8380ee81c921a2867d522f288123")
set(_pinned_files
    "src/libslic3r/CMakeLists.txt|32335b375c6af7dc5f77e7237f6cffbe242873d46fa85d1070943215808012da"
    "src/libslic3r/Layer.hpp|0336a4a9bdf8b80cebe08320d48112014a441b58d98439e85b2a927323d6e7bf"
    "src/libslic3r/Print.hpp|ea5bc36f78d3fc5faed55caf0ad3791085d8ad40b523f64e27b86babd916f9db"
    "src/libslic3r/PrintConfig.cpp|74b08a0502595afce5bc389c2b6ed59de8469efc810d1f637b7ce9255686b513"
    "src/libslic3r/PrintObject.cpp|d7d64379f88b1406cb658e6ccb6c263dd694a567ff3939b3d669bd84364670ed"
    "src/libslic3r/Support/OrganicSupport.cpp|5cae2a3d005ba219083af4adb575cd7262dc6b5f6e43f638b3fa0173123a57b2"
    "src/libslic3r/Support/OrganicSupport.hpp|0de5dc7a5adcfa341e9ee07b55aa0f2c6821d0b09e18edd007942d1f263c8c75"
    "src/libslic3r/Support/SupportCommon.cpp|1f2c18a667e937e44be293662a6f90ea39fd04f166d4772679d185bf9a7e4290"
    "src/libslic3r/Support/SupportCommon.hpp|fa69b9484ecaee7e84d11f392fcdf15d6b4062de97dff5c0a84920685c1d0ee9"
    "src/libslic3r/Support/SupportLayer.hpp|4a451de5fd2480d5be868627536d2e111dc2707d7e48c59562cb82574092cff3"
    "src/libslic3r/Support/SupportParameters.cpp|1a4fbf59ad899a4aa6404836b88f14617ea90b0c1443a1ada225bd1ee1491488"
    "src/libslic3r/Support/SupportParameters.hpp|7d651d02230a3b686735a1c980d3b8874fb1127c96a5dff2b9d23e71e8ad7641"
    "src/libslic3r/Support/TreeModelVolumes.cpp|b5aa5f5119dbd841a1d30a30f79b70face3dae320fc7a189095388e33eb10279"
    "src/libslic3r/Support/TreeModelVolumes.hpp|3f474cbfe51d55151d58970906e2a141330ad3444fc24a995108a78ba176c5ba"
    "src/libslic3r/Support/TreeSupport.cpp|44e89ee342c746862db72ec2ace5768a68b71f1a79e206d53264d2184fd337e6"
    "src/libslic3r/Support/TreeSupport.hpp|8d19e5b1dd92adad0f8f741d2d14353b96e282efcdb2408077798e8fa6860636"
    "src/libslic3r/Support/TreeSupportCommon.cpp|e4226b77345950b7463be95ef0d410214707c3c5ba1d8313fbca4426e52d94a2"
    "src/libslic3r/Support/TreeSupportCommon.hpp|99a80f31dfb6ea982709a4cbb9ba5001b45d12f07e3678ce0154da7aca9ff6a9")

if(NOT PRUSASLICER_SOURCE_DIR)
    message(FATAL_ERROR
        "PRUSASLICER_SOURCE_DIR is required. Point it at a complete checkout of "
        "PrusaSlicer version_2.9.6 (${_prusa_commit}). See README.md.")
endif()

get_filename_component(PRUSASLICER_SOURCE_DIR "${PRUSASLICER_SOURCE_DIR}" REALPATH)
if(NOT EXISTS "${PRUSASLICER_SOURCE_DIR}/src/libslic3r/Support/OrganicSupport.cpp")
    message(FATAL_ERROR
        "PRUSASLICER_SOURCE_DIR is not a complete PrusaSlicer source tree: "
        "${PRUSASLICER_SOURCE_DIR}")
endif()

# Refuse to compile against a similar-looking implementation. Check both the
# Git identity (when available) and every isolated pipeline file's content.
find_package(Git QUIET)
if(Git_FOUND AND EXISTS "${PRUSASLICER_SOURCE_DIR}/.git")
    execute_process(
        COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
        WORKING_DIRECTORY "${PRUSASLICER_SOURCE_DIR}"
        OUTPUT_VARIABLE _actual_commit
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _git_result)
    if(NOT _git_result EQUAL 0)
        message(FATAL_ERROR "Could not identify the PrusaSlicer checkout commit")
    endif()
    if(NOT _actual_commit STREQUAL _prusa_commit)
        message(FATAL_ERROR
            "Wrong PrusaSlicer revision: ${_actual_commit}. Expected ${_prusa_commit}.")
    endif()
endif()

foreach(_pinned_file IN LISTS _pinned_files)
    string(REPLACE "|" ";" _pinned_parts "${_pinned_file}")
    list(GET _pinned_parts 0 _relative_path)
    list(GET _pinned_parts 1 _expected_sha256)
    file(SHA256 "${PRUSASLICER_SOURCE_DIR}/${_relative_path}" _actual_sha256)
    if(NOT _actual_sha256 STREQUAL _expected_sha256)
        message(FATAL_ERROR
            "${_relative_path} does not match pinned PrusaSlicer 2.9.6. "
            "Expected ${_expected_sha256}, got ${_actual_sha256}.")
    endif()
endforeach()

# Build only the headless native graph. These cache entries are consumed by the
# unmodified upstream project added below.
set(SLIC3R_GUI OFF CACHE BOOL "" FORCE)
set(SLIC3R_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SLIC3R_BUILD_SANDBOXES OFF CACHE BOOL "" FORCE)
set(SLIC3R_ENABLE_FORMAT_STEP OFF CACHE BOOL "" FORCE)

# These optimizations change only the compiled representation, not the normal
# Print::process() / Organic-support execution path. Apply the compile flags
# before importing upstream so static archive members can be discarded too.
if(HOLDERPRO_ENABLE_LTO)
    include(CheckIPOSupported)
    check_ipo_supported(RESULT _holderpro_ipo_supported
                        OUTPUT _holderpro_ipo_error
                        LANGUAGES C CXX)
    if(_holderpro_ipo_supported)
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ON)
    else()
        message(WARNING
            "Release LTO is unavailable with this toolchain: ${_holderpro_ipo_error}")
    endif()
endif()

if(HOLDERPRO_ENABLE_DEAD_CODE_ELIMINATION)
    if(MSVC)
        add_compile_options($<$<CONFIG:Release>:/Gy> $<$<CONFIG:Release>:/Gw>)
    else()
        add_compile_options(
            $<$<CONFIG:Release>:-ffunction-sections>
            $<$<CONFIG:Release>:-fdata-sections>)
    endif()
endif()

# Upstream links this target unconditionally on Apple even when STEP support is
# disabled. The adapter calls only the STL/OBJ/3MF loaders, so an empty target
# accurately represents that intentionally absent optional format backend.
if(APPLE AND NOT TARGET OCCTWrapper)
    add_library(OCCTWrapper INTERFACE)
endif()

add_subdirectory("${PRUSASLICER_SOURCE_DIR}" prusaslicer EXCLUDE_FROM_ALL)

add_executable(holderpro-organic-engine src/main.cpp)
target_compile_features(holderpro-organic-engine PRIVATE cxx_std_17)
target_compile_definitions(holderpro-organic-engine PRIVATE
    HOLDERPRO_VERSION="${HOLDERPRO_VERSION}"
    HOLDERPRO_ADAPTER_VERSION="${HOLDERPRO_ADAPTER_VERSION}"
    HOLDERPRO_BUILD_ID="${HOLDERPRO_BUILD_ID}"
    HOLDERPRO_PRUSASLICER_COMMIT="${_prusa_commit}"
    HOLDERPRO_PRUSASLICER_VERSION="2.9.6")
target_link_libraries(holderpro-organic-engine PRIVATE libslic3r)

add_executable(holderpro-private-output-test EXCLUDE_FROM_ALL
    tests/private-output.cpp)
target_compile_features(holderpro-private-output-test PRIVATE cxx_std_17)
target_include_directories(holderpro-private-output-test PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src")

if(WIN32)
    # OpenProcessToken and the security-descriptor APIs used to create native
    # staging paths with a protected, owner-only DACL are provided by Advapi32.
    target_link_libraries(holderpro-organic-engine PRIVATE Advapi32)
    target_link_libraries(holderpro-private-output-test PRIVATE Advapi32)
endif()

if(MSVC)
    target_link_options(holderpro-organic-engine PRIVATE
        "/MANIFEST:EMBED"
        "/MANIFESTINPUT:${CMAKE_CURRENT_SOURCE_DIR}/windows/holderpro-organic-engine.manifest")
endif()

if(HOLDERPRO_ENABLE_DEAD_CODE_ELIMINATION)
    if(MSVC)
        target_link_options(holderpro-organic-engine PRIVATE
            $<$<CONFIG:Release>:/OPT:REF>
            $<$<CONFIG:Release>:/OPT:ICF>)
    elseif(APPLE)
        target_link_options(holderpro-organic-engine PRIVATE
            $<$<CONFIG:Release>:-Wl,-dead_strip>)
    elseif(UNIX)
        target_link_options(holderpro-organic-engine PRIVATE
            $<$<CONFIG:Release>:-Wl,--gc-sections>)
    endif()
endif()

include(CTest)
if(BUILD_TESTING)
    add_test(NAME holderpro-organic-engine-help
        COMMAND holderpro-organic-engine --help)
    add_test(NAME holderpro-organic-engine-version-json
        COMMAND holderpro-organic-engine --version-json)
    add_test(NAME holderpro-organic-engine-input-output-alias
        COMMAND "${CMAKE_COMMAND}"
            "-DHOLDERPRO_ENGINE=$<TARGET_FILE:holderpro-organic-engine>"
            "-DHOLDERPRO_TEST_DIR=${CMAKE_CURRENT_BINARY_DIR}/alias-safety-test"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/input-output-alias.cmake")
    add_test(NAME holderpro-private-output-security
        COMMAND holderpro-private-output-test
            "${CMAKE_CURRENT_BINARY_DIR}/private-output-security-test")
    set_tests_properties(holderpro-organic-engine-version-json PROPERTIES
        PASS_REGULAR_EXPRESSION
            "HolderPro.*2.9.6.*${_prusa_commit}.*holderpro.organic-support-layers/v1")
    if(WIN32 AND HOLDERPRO_RUNTIME_DEPENDENCY_DIR)
        set_tests_properties(
            holderpro-organic-engine-help
            holderpro-organic-engine-version-json
            holderpro-organic-engine-input-output-alias
            holderpro-private-output-security
            PROPERTIES ENVIRONMENT_MODIFICATION
                "PATH=path_list_prepend:${HOLDERPRO_RUNTIME_DEPENDENCY_DIR}")
    endif()
endif()

# Release jobs install to a staging directory with `cmake --install --strip`
# before dependency auditing, signing, and packaging. The build-tree binary is
# deliberately left unstripped so local failures remain diagnosable. Windows
# Prusa dependencies include runtime DLLs (notably GMP), so collect every
# non-system transitive runtime next to the executable for wheels/installers.
if(WIN32)
    install(TARGETS holderpro-organic-engine
        RUNTIME_DEPENDENCIES
            DIRECTORIES "${HOLDERPRO_RUNTIME_DEPENDENCY_DIR}"
            PRE_EXCLUDE_REGEXES "api-ms-win-.*" "ext-ms-.*"
            POST_EXCLUDE_REGEXES
                ".*[Ww][Ii][Nn][Dd][Oo][Ww][Ss][/\\\\][Ss][Yy][Ss][Tt][Ee][Mm]32.*"
        RUNTIME DESTINATION bin)
else()
    install(TARGETS holderpro-organic-engine RUNTIME DESTINATION bin)
endif()
