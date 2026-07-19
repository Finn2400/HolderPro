// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Thin, headless adapter around PrusaSlicer's unmodified Organic support
// pipeline. The filled support footprints are exported before any downstream
// consumer decides how to turn them into a printable solid.

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include "libslic3r/Config.hpp"
#include "libslic3r/ExtrusionEntity.hpp"
#include "libslic3r/ExtrusionEntityCollection.hpp"
#include "libslic3r/Format/3mf.hpp"
#include "libslic3r/Format/OBJ.hpp"
#include "libslic3r/Format/STL.hpp"
#include "libslic3r/Layer.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/TriangleSelector.hpp"
#include "libslic3r/libslic3r.h"

#include "private_output.hpp"

#include <boost/nowide/fstream.hpp>
#include <boost/optional.hpp>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#ifndef HOLDERPRO_PRUSASLICER_COMMIT
#define HOLDERPRO_PRUSASLICER_COMMIT "unknown"
#endif

#ifndef HOLDERPRO_PRUSASLICER_VERSION
#define HOLDERPRO_PRUSASLICER_VERSION "unknown"
#endif

#ifndef HOLDERPRO_VERSION
#define HOLDERPRO_VERSION "0+unknown"
#endif

#ifndef HOLDERPRO_ADAPTER_VERSION
#define HOLDERPRO_ADAPTER_VERSION "unknown"
#endif

#ifndef HOLDERPRO_BUILD_ID
#define HOLDERPRO_BUILD_ID "local"
#endif

namespace {

using Slic3r::ConfigSubstitutionContext;
using Slic3r::DynamicPrintConfig;
using Slic3r::ExPolygon;
using Slic3r::ForwardCompatibilitySubstitutionRule;
using Slic3r::Model;
using Slic3r::ModelVolume;
using Slic3r::Point;
using Slic3r::Polygon;
using Slic3r::Print;
using Slic3r::PrintBase;
using Slic3r::PrintObject;
using Slic3r::Semver;
using Slic3r::SupportLayer;
using Slic3r::TriangleSelector;
using Slic3r::TriangleStateType;

constexpr std::string_view kSchema = "holderpro.organic-support-layers/v1";
constexpr std::string_view kPaintSchema = "HOLDERPRO_SUPPORT_PAINT_V1";

struct Override {
    std::string key;
    std::string value;
};

struct Options {
    std::filesystem::path input;
    std::filesystem::path output;
    std::optional<std::filesystem::path> config;
    std::optional<std::filesystem::path> support_paint;
    std::vector<Override> overrides;
    bool enforcers_only = false;
    bool validate_solid = false;
    bool quiet = false;
    bool help = false;
    bool version_json = false;
};

[[noreturn]] void usage_error(const std::string &message)
{
    throw std::invalid_argument(
        message + "\nRun holderpro-organic-engine --help for usage.");
}

std::string usage()
{
    return R"USAGE(Exact PrusaSlicer Organic support layer generator

Usage:
  holderpro-organic-engine --input MODEL --output SUPPORTS.json [options]
  holderpro-organic-engine --validate-solid SUPPORTS.stl
  holderpro-organic-engine --version-json

Required:
  --input PATH                 STL, OBJ, or 3MF model
  --output PATH                Filled support-layer JSON

Organic support settings:
  --layer-height MM            Object/support layer height (default: 0.3)
  --branch-diameter MM         Minimum branch diameter (default: 2.0)
  --tip-diameter MM            Branch tip diameter (default: 0.8)
  --branch-angle DEG           Maximum branch angle (default: 40)
  --branch-angle-slow DEG      Preferred branch angle (default: 25)
  --contact-distance MM        Top contact Z distance (default: 0.0 here)

Configuration:
  --config PATH                Load a PrusaSlicer INI configuration
  --set KEY=VALUE              Override any existing PrusaSlicer option;
                               may be repeated
  --support-paint PATH         Apply HOLDERPRO_SUPPORT_PAINT_V1 face states
  --enforcers-only             Disable automatic overhang detection

Other:
  --validate-solid PATH        Load and repair-check a printable support STL
  --quiet                      Suppress slicing progress on stderr
  --version-json               Print machine-readable provenance and exit
  -h, --help                   Show this help

The engine always forces support_material=1, support_material_style=organic,
and raft_layers=0.
It does not arrange, center, rotate, or move the input model onto the bed.
)USAGE";
}

double parse_number(const std::string &flag, const std::string &text)
{
    std::size_t consumed = 0;
    double value = 0.0;
    try {
        value = std::stod(text, &consumed);
    } catch (const std::exception &) {
        usage_error(flag + " expects a number, got '" + text + "'");
    }
    if (consumed != text.size() || !std::isfinite(value))
        usage_error(flag + " expects a finite number, got '" + text + "'");
    return value;
}

std::filesystem::path path_from_utf8(const std::string &value)
{
    return std::filesystem::u8path(value);
}

std::string path_to_utf8(const std::filesystem::path &value)
{
    return value.u8string();
}

std::filesystem::path absolute_lexical_path(
    const std::filesystem::path &path,
    std::string_view option)
{
    std::error_code error;
    std::filesystem::path absolute = std::filesystem::absolute(path, error);
    if (error)
        throw std::invalid_argument(
            "cannot safely resolve " + std::string(option) + ": " +
            error.message());
    return absolute.lexically_normal();
}

void reject_input_output_alias(const std::filesystem::path &input,
                               const std::filesystem::path &output)
{
    // Keep the lexical check for a not-yet-created output, then ask the
    // filesystem whether two existing names identify the same file. The
    // latter covers hard links, symlinks, case-insensitive aliases, and the
    // platform's native Unicode filename equivalence rules.
    if (absolute_lexical_path(input, "--input") ==
        absolute_lexical_path(output, "--output"))
        throw std::invalid_argument("--output must not overwrite --input");

    std::error_code exists_error;
    const bool output_exists = std::filesystem::exists(output, exists_error);
    if (exists_error)
        throw std::invalid_argument(
            "cannot safely inspect --output before writing: " +
            exists_error.message());
    if (!output_exists)
        return;

    std::error_code equivalent_error;
    const bool aliases_input =
        std::filesystem::equivalent(input, output, equivalent_error);
    if (equivalent_error)
        throw std::invalid_argument(
            "cannot safely determine whether --output aliases --input: " +
            equivalent_error.message());
    if (aliases_input)
        throw std::invalid_argument("--output must not overwrite --input");
}

std::string require_value(std::size_t &index,
                          const std::vector<std::string> &arguments,
                          const std::string &flag)
{
    if (++index >= arguments.size())
        usage_error(flag + " requires a value");
    return arguments[index];
}

void add_numeric_override(Options &options,
                          const std::string &key,
                          const std::string &flag,
                          const std::string &value)
{
    parse_number(flag, value);
    options.overrides.push_back({key, value});
}

Options parse_options(const std::vector<std::string> &arguments)
{
    Options options;
    for (std::size_t i = 1; i < arguments.size(); ++i) {
        const std::string &arg = arguments[i];
        if (arg == "-h" || arg == "--help") {
            options.help = true;
        } else if (arg == "--version-json") {
            options.version_json = true;
        } else if (arg == "--input") {
            options.input = path_from_utf8(require_value(i, arguments, arg));
        } else if (arg == "--validate-solid") {
            options.input = path_from_utf8(require_value(i, arguments, arg));
            options.validate_solid = true;
        } else if (arg == "--output") {
            options.output = path_from_utf8(require_value(i, arguments, arg));
        } else if (arg == "--config") {
            options.config = path_from_utf8(require_value(i, arguments, arg));
        } else if (arg == "--support-paint") {
            options.support_paint = path_from_utf8(require_value(i, arguments, arg));
        } else if (arg == "--layer-height") {
            add_numeric_override(options, "layer_height", arg,
                                 require_value(i, arguments, arg));
        } else if (arg == "--branch-diameter") {
            add_numeric_override(options, "support_tree_branch_diameter", arg,
                                 require_value(i, arguments, arg));
        } else if (arg == "--tip-diameter") {
            add_numeric_override(options, "support_tree_tip_diameter", arg,
                                 require_value(i, arguments, arg));
        } else if (arg == "--branch-angle") {
            add_numeric_override(options, "support_tree_angle", arg,
                                 require_value(i, arguments, arg));
        } else if (arg == "--branch-angle-slow") {
            add_numeric_override(options, "support_tree_angle_slow", arg,
                                 require_value(i, arguments, arg));
        } else if (arg == "--contact-distance") {
            add_numeric_override(options, "support_material_contact_distance", arg,
                                 require_value(i, arguments, arg));
        } else if (arg == "--set") {
            const std::string expression = require_value(i, arguments, arg);
            const std::size_t equals = expression.find('=');
            if (equals == std::string::npos || equals == 0)
                usage_error("--set expects KEY=VALUE, got '" + expression + "'");
            options.overrides.push_back(
                {expression.substr(0, equals), expression.substr(equals + 1)});
        } else if (arg == "--enforcers-only") {
            options.enforcers_only = true;
        } else if (arg == "--quiet") {
            options.quiet = true;
        } else {
            usage_error("unknown argument '" + arg + "'");
        }
    }

    if (!options.help && !options.version_json) {
        if (options.input.empty())
            usage_error("--input is required");
        if (!options.validate_solid && options.output.empty())
            usage_error("--output is required");
    }
    return options;
}

std::string lowercase(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

void check_input_extension(const std::filesystem::path &path)
{
    const std::string extension = lowercase(path_to_utf8(path.extension()));
    if (extension != ".stl" && extension != ".obj" && extension != ".3mf")
        throw std::runtime_error(
            "unsupported input format '" + extension + "'; expected STL, OBJ, or 3MF");
}

std::string json_escape(std::string_view value)
{
    std::ostringstream out;
    for (unsigned char c : value) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<int>(c) << std::dec;
            } else {
                out << static_cast<char>(c);
            }
        }
    }
    return out.str();
}

constexpr std::string_view operating_system()
{
#if defined(_WIN32)
    return "windows";
#elif defined(__APPLE__)
    return "macos";
#elif defined(__linux__)
    return "linux";
#elif defined(__FreeBSD__)
    return "freebsd";
#else
    return "unknown";
#endif
}

constexpr std::string_view architecture()
{
#if defined(__aarch64__) || defined(_M_ARM64)
    return "arm64";
#elif defined(__x86_64__) || defined(_M_X64)
    return "x86_64";
#elif defined(__i386__) || defined(_M_IX86)
    return "x86";
#elif defined(__arm__) || defined(_M_ARM)
    return "arm";
#else
    return "unknown";
#endif
}

void write_version_json(std::ostream &out)
{
    out << "{\"product\":{\"name\":\"HolderPro\",\"version\":\""
        << json_escape(HOLDERPRO_VERSION)
        << "\"},\"adapter\":{\"name\":\"holderpro-organic-engine\","
           "\"version\":\""
        << json_escape(HOLDERPRO_ADAPTER_VERSION)
        << "\"},\"prusaslicer\":{\"version\":\""
        << json_escape(HOLDERPRO_PRUSASLICER_VERSION)
        << "\",\"commit\":\"" << json_escape(HOLDERPRO_PRUSASLICER_COMMIT)
        << "\"},\"schemas\":{\"layers\":\"" << json_escape(kSchema)
        << "\",\"paint\":\"" << json_escape(kPaintSchema)
        << "\"},\"os\":\"" << operating_system()
        << "\",\"architecture\":\"" << architecture()
        << "\",\"build_id\":\"" << json_escape(HOLDERPRO_BUILD_ID)
        << "\"}\n";
}

Model load_model_and_project_config(const Options &options,
                                    DynamicPrintConfig &project_config)
{
    if (!std::filesystem::is_regular_file(options.input))
        throw std::runtime_error(
            "input file does not exist: " + path_to_utf8(options.input));
    check_input_extension(options.input);

    const std::string input = path_to_utf8(options.input);
    const std::string extension = lowercase(path_to_utf8(options.input.extension()));
    Model model;
    bool loaded = false;
    if (extension == ".stl") {
        loaded = Slic3r::load_stl(input.c_str(), &model);
    } else if (extension == ".obj") {
        loaded = Slic3r::load_obj(input.c_str(), &model);
    } else {
        ConfigSubstitutionContext substitutions(
            ForwardCompatibilitySubstitutionRule::Enable);
        boost::optional<Semver> generator_version;
        loaded = Slic3r::load_3mf(input.c_str(),
                                  project_config,
                                  substitutions,
                                  &model,
                                  false,
                                  generator_version);
    }
    if (!loaded)
        throw std::runtime_error("PrusaSlicer could not load input: " + input);

    for (Slic3r::ModelObject *object : model.objects)
        object->input_file = input;
    model.add_default_instances();
    return model;
}

DynamicPrintConfig make_config(const Options &options,
                               const DynamicPrintConfig &project_config)
{
    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    config.apply(project_config, true);

    if (options.config) {
        if (!std::filesystem::is_regular_file(*options.config))
            throw std::runtime_error("config file does not exist: " +
                                     path_to_utf8(*options.config));
        DynamicPrintConfig loaded;
        loaded.load(path_to_utf8(*options.config),
                    ForwardCompatibilitySubstitutionRule::Enable);
        loaded.normalize_fdm();
        config.apply(loaded, true);
    }

    // The supports-only tool intentionally differs from generic Prusa defaults
    // only in enabling Organic supports and using a zero top gap. Every value
    // remains a normal upstream configuration option.
    config.set_deserialize_strict({
        {"printer_technology", "FFF"},
        {"raft_layers", "0"},
        {"support_material", true},
        {"support_material_auto", !options.enforcers_only},
        {"support_material_style", "organic"},
        {"support_material_contact_distance", "0"},
    });

    for (const Override &item : options.overrides)
        config.set_deserialize_strict(item.key, item.value);

    // These are invariants, not user-tunable approximations. Apply them last so
    // neither a project config nor --set can switch to Grid/Snug or disable the
    // genuine Organic generator accidentally.
    config.set_deserialize_strict("support_material", "1");
    config.set_deserialize_strict("support_material_style", "organic");
    config.set_deserialize_strict("raft_layers", "0");
    config.set_deserialize_strict("support_material_auto",
                                  options.enforcers_only ? "0" : "1");
    config.normalize_fdm();

    const std::string error = config.validate();
    if (!error.empty())
        throw std::runtime_error("invalid PrusaSlicer configuration: " + error);
    return config;
}

void validate_minimal_model(const Model &model)
{
    if (model.objects.empty())
        throw std::runtime_error("input model contains no objects");
    if (model.objects.size() != 1)
        throw std::runtime_error(
            "v1 accepts exactly one model object; merge the model before generation");
    if (model.objects.front()->instances.size() != 1)
        throw std::runtime_error(
            "v1 accepts exactly one model instance; merge or remove copies before generation");
}

void apply_support_paint(Model &model, const std::filesystem::path &path)
{
    if (!std::filesystem::is_regular_file(path))
        throw std::runtime_error(
            "support paint file does not exist: " + path_to_utf8(path));

    std::vector<ModelVolume *> model_parts;
    for (Slic3r::ModelObject *object : model.objects) {
        for (ModelVolume *volume : object->volumes) {
            if (volume->is_model_part())
                model_parts.push_back(volume);
        }
    }
    if (model_parts.size() != 1)
        throw std::runtime_error(
            "support painting requires exactly one model-part volume");

    ModelVolume &volume = *model_parts.front();
    const std::size_t face_count = volume.mesh().its.indices.size();
    if (face_count > static_cast<std::size_t>(std::numeric_limits<int>::max()))
        throw std::runtime_error("support-painted mesh has too many faces");

    boost::nowide::ifstream input(path_to_utf8(path));
    if (!input)
        throw std::runtime_error(
            "cannot open support paint file: " + path_to_utf8(path));

    std::string line;
    if (!std::getline(input, line) || line != "HOLDERPRO_SUPPORT_PAINT_V1")
        throw std::runtime_error("unrecognized support paint format");
    if (!std::getline(input, line))
        throw std::runtime_error("support paint file has no face count");
    {
        std::istringstream header(line);
        std::string keyword;
        std::size_t declared_count = 0;
        std::string trailing;
        if (!(header >> keyword >> declared_count) || keyword != "faces" ||
            (header >> trailing))
            throw std::runtime_error("invalid support paint face-count line");
        if (declared_count != face_count)
            throw std::runtime_error(
                "support paint face count does not match the loaded mesh (paint=" +
                std::to_string(declared_count) + ", mesh=" +
                std::to_string(face_count) + ")");
    }

    std::vector<unsigned char> states(face_count, 0);
    std::size_t enforcer_count = 0;
    std::size_t blocker_count = 0;
    std::size_t line_number = 2;
    while (std::getline(input, line)) {
        ++line_number;
        if (line.empty())
            continue;
        std::istringstream record(line);
        char state = '\0';
        std::size_t face = 0;
        std::string trailing;
        if (!(record >> state >> face) || (record >> trailing) ||
            (state != 'E' && state != 'B'))
            throw std::runtime_error(
                "invalid support paint record on line " +
                std::to_string(line_number));
        if (face >= face_count)
            throw std::runtime_error(
                "support paint face index out of range on line " +
                std::to_string(line_number));
        if (states[face] != 0)
            throw std::runtime_error(
                "duplicate support paint face on line " +
                std::to_string(line_number));
        states[face] = state == 'E' ? 1 : 2;
        if (state == 'E')
            ++enforcer_count;
        else
            ++blocker_count;
    }
    if (!input.eof())
        throw std::runtime_error("failed while reading support paint file");

    TriangleSelector selector(volume.mesh());
    for (std::size_t face = 0; face < states.size(); ++face) {
        if (states[face] == 1)
            selector.set_facet(static_cast<int>(face), TriangleStateType::ENFORCER);
        else if (states[face] == 2)
            selector.set_facet(static_cast<int>(face), TriangleStateType::BLOCKER);
    }
    volume.supported_facets.set(selector);
    std::cerr << "[holderpro-organic-engine] applied " << enforcer_count
              << " enforcer and " << blocker_count
              << " blocker facets from " << path_to_utf8(path) << '\n';
}

void attach_progress(Print &print, bool quiet)
{
    if (quiet) {
        print.set_status_silent();
        return;
    }

    print.set_status_callback([](const PrintBase::SlicingStatus &status) {
        static std::mutex mutex;
        static int last_percent = std::numeric_limits<int>::min();
        static std::string last_text;
        if (status.percent < 0)
            return;
        std::lock_guard<std::mutex> lock(mutex);
        if (status.percent == last_percent && status.text == last_text)
            return;
        last_percent = status.percent;
        last_text = status.text;
        std::cerr << "[holderpro-organic-engine] " << status.percent << "%";
        if (!status.text.empty())
            std::cerr << " " << status.text;
        std::cerr << '\n';
    });
}

void write_ring(std::ostream &out, const Polygon &ring, const Point &shift)
{
    out << '[';
    for (std::size_t i = 0; i < ring.points.size(); ++i) {
        if (i != 0)
            out << ',';
        const Point point = ring.points[i] + shift;
        out << '[' << Slic3r::unscale<double>(point.x()) << ','
            << Slic3r::unscale<double>(point.y()) << ']';
    }
    out << ']';
}

void write_polygon(std::ostream &out, const ExPolygon &polygon, const Point &shift)
{
    out << "{\"contour\":";
    write_ring(out, polygon.contour, shift);
    out << ",\"holes\":[";
    for (std::size_t i = 0; i < polygon.holes.size(); ++i) {
        if (i != 0)
            out << ',';
        write_ring(out, polygon.holes[i], shift);
    }
    out << "]}";
}

struct ExportSummary {
    std::size_t layer_count = 0;
    std::size_t nonempty_layer_count = 0;
    std::size_t polygon_count = 0;
    std::size_t point_count = 0;
};

// SupportLayer::support_islands is a union of all support-generator source
// footprints at the layer's print_z.  That union can only be represented as a
// single filled slab when every generated extrusion has the same height as the
// SupportLayer.  Prove that invariant from the unmodified upstream toolpaths
// before exporting; otherwise fail instead of silently losing mixed heights.
struct ExtrusionHeightProof {
    std::size_t path_count = 0;
    double minimum = std::numeric_limits<double>::infinity();
    double maximum = -std::numeric_limits<double>::infinity();
};

void observe_extrusion_path(const Slic3r::ExtrusionPath &path,
                            ExtrusionHeightProof &proof)
{
    const double height = path.height();
    if (!std::isfinite(height) || height <= 0.0)
        throw std::runtime_error(
            "cannot prove filled support-layer height: generated extrusion path "
            "has a non-finite or non-positive height");
    ++proof.path_count;
    proof.minimum = std::min(proof.minimum, height);
    proof.maximum = std::max(proof.maximum, height);
}

void collect_extrusion_heights(const Slic3r::ExtrusionEntity &entity,
                               ExtrusionHeightProof &proof)
{
    if (const auto *collection =
            dynamic_cast<const Slic3r::ExtrusionEntityCollection *>(&entity)) {
        for (const Slic3r::ExtrusionEntity *child : collection->entities) {
            if (child == nullptr)
                throw std::runtime_error(
                    "cannot prove filled support-layer height: null extrusion entity");
            collect_extrusion_heights(*child, proof);
        }
    } else if (const auto *path =
                   dynamic_cast<const Slic3r::ExtrusionPath *>(&entity)) {
        observe_extrusion_path(*path, proof);
    } else if (const auto *multipath =
                   dynamic_cast<const Slic3r::ExtrusionMultiPath *>(&entity)) {
        for (const Slic3r::ExtrusionPath &path : multipath->paths)
            observe_extrusion_path(path, proof);
    } else if (const auto *loop =
                   dynamic_cast<const Slic3r::ExtrusionLoop *>(&entity)) {
        for (const Slic3r::ExtrusionPath &path : loop->paths)
            observe_extrusion_path(path, proof);
    } else {
        throw std::runtime_error(
            "cannot prove filled support-layer height: unknown extrusion entity type");
    }
}

ExtrusionHeightProof prove_uniform_support_heights(const PrintObject &object)
{
    constexpr double kHeightTolerance = 1e-5;
    ExtrusionHeightProof all_layers;

    for (const SupportLayer *layer : object.support_layers()) {
        if (layer->support_islands.empty())
            continue;
        if (!std::isfinite(layer->height) || layer->height <= 0.0)
            throw std::runtime_error(
                "cannot export filled support layer at print_z=" +
                std::to_string(layer->print_z) +
                ": layer height is non-finite or non-positive");

        ExtrusionHeightProof layer_proof;
        collect_extrusion_heights(layer->support_fills, layer_proof);
        if (layer_proof.path_count == 0)
            throw std::runtime_error(
                "cannot prove filled support-layer height at print_z=" +
                std::to_string(layer->print_z) +
                ": support islands have no generated extrusion paths");

        const double difference = std::max(
            std::abs(layer_proof.minimum - layer->height),
            std::abs(layer_proof.maximum - layer->height));
        if (difference > kHeightTolerance) {
            std::ostringstream message;
            message << std::fixed << std::setprecision(6)
                    << "cannot losslessly export mixed-height support layer at print_z="
                    << layer->print_z << ": layer height=" << layer->height
                    << ", observed extrusion heights=" << layer_proof.minimum
                    << ".." << layer_proof.maximum
                    << " (tolerance " << kHeightTolerance << ')';
            throw std::runtime_error(message.str());
        }

        all_layers.path_count += layer_proof.path_count;
        all_layers.minimum = std::min(all_layers.minimum, layer_proof.minimum);
        all_layers.maximum = std::max(all_layers.maximum, layer_proof.maximum);
    }
    return all_layers;
}

ExportSummary summarize(const PrintObject &object)
{
    ExportSummary summary;
    summary.layer_count = object.support_layers().size();
    for (const SupportLayer *layer : object.support_layers()) {
        if (!layer->support_islands.empty())
            ++summary.nonempty_layer_count;
        summary.polygon_count += layer->support_islands.size();
        for (const ExPolygon &polygon : layer->support_islands) {
            summary.point_count += polygon.contour.points.size();
            for (const Polygon &hole : polygon.holes)
                summary.point_count += hole.points.size();
        }
    }
    return summary;
}

void replace_file_atomically(const std::filesystem::path &temporary,
                             const std::filesystem::path &destination)
{
#ifdef _WIN32
    // The files are siblings, so MoveFileEx performs a same-volume replacement
    // without the delete-then-rename window exposed by std::filesystem on
    // Windows. WRITE_THROUGH also waits for the replacement to reach disk.
    if (!MoveFileExW(temporary.c_str(), destination.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        const std::error_code error(
            static_cast<int>(GetLastError()), std::system_category());
        throw std::runtime_error("cannot atomically finalize output: " +
                                 error.message());
    }
#else
    std::error_code error;
    std::filesystem::rename(temporary, destination, error);
    if (error)
        throw std::runtime_error("cannot atomically finalize output: " +
                                 error.message());
#endif
}

void write_json(const Options &options, const Print &print)
{
    if (print.objects().size() != 1)
        throw std::runtime_error(
            "internal error: expected exactly one generated print object");
    const PrintObject &object = *print.objects().front();
    if (object.instances().size() != 1)
        throw std::runtime_error(
            "internal error: expected exactly one generated print instance");
    const Point shift = object.instances().front().shift;
    const ExtrusionHeightProof height_proof = prove_uniform_support_heights(object);
    const ExportSummary summary = summarize(object);

    const std::filesystem::path parent = options.output.parent_path();
    if (!parent.empty() && !std::filesystem::exists(parent))
        std::filesystem::create_directories(parent);

    holderpro::native::PrivateTemporaryDirectory temporary_directory(options.output);
    const std::filesystem::path temporary =
        temporary_directory.path() / "support-layers.json";
    holderpro::native::reserve_private_output_file(temporary);
    boost::nowide::ofstream out(path_to_utf8(temporary),
                                std::ios::binary | std::ios::trunc);
    if (!out)
        throw std::runtime_error("cannot open output: " + path_to_utf8(temporary));
    out << std::fixed << std::setprecision(6);

    out << "{\n"
        << "  \"schema\":\"" << kSchema << "\",\n"
        << "  \"version\":1,\n"
        << "  \"engine\":{\"name\":\"PrusaSlicer Organic\","
        << "\"version\":\"" << HOLDERPRO_PRUSASLICER_VERSION << "\","
        << "\"commit\":\"" << HOLDERPRO_PRUSASLICER_COMMIT << "\"},\n"
        << "  \"units\":\"mm\",\n"
        << "  \"input\":\"" << json_escape(path_to_utf8(options.input)) << "\",\n"
        << "  \"layers\":[";

    bool first_layer = true;
    for (const SupportLayer *layer : object.support_layers()) {
        if (!first_layer)
            out << ',';
        first_layer = false;
        out << "\n    {\"print_z\":" << layer->print_z
            << ",\"height\":" << layer->height
            << ",\"bottom_z\":" << layer->bottom_z()
            << ",\"polygons\":[";
        for (std::size_t i = 0; i < layer->support_islands.size(); ++i) {
            if (i != 0)
                out << ',';
            write_polygon(out, layer->support_islands[i], shift);
        }
        out << "]}";
    }

    if (!first_layer)
        out << '\n';
    out << "  ],\n"
        << "  \"summary\":{\"layer_count\":" << summary.layer_count
        << ",\"nonempty_layer_count\":" << summary.nonempty_layer_count
        << ",\"polygon_count\":" << summary.polygon_count
        << ",\"point_count\":" << summary.point_count << "}\n"
        << "}\n";
    out.close();
    if (!out)
        throw std::runtime_error(
            "failed while writing output: " + path_to_utf8(temporary));

    replace_file_atomically(temporary, options.output);

    std::cerr << "[holderpro-organic-engine] wrote " << summary.polygon_count
              << " filled polygons on " << summary.layer_count << " layers to "
              << path_to_utf8(options.output);
    if (height_proof.path_count != 0)
        std::cerr << " (proved " << height_proof.path_count
                  << " extrusion paths, heights " << height_proof.minimum
                  << ".." << height_proof.maximum << " mm)";
    std::cerr << '\n';
}

int run(const Options &options)
{
    if (options.help) {
        std::cout << usage();
        return EXIT_SUCCESS;
    }
    if (options.version_json) {
        write_version_json(std::cout);
        return EXIT_SUCCESS;
    }

    if (!options.validate_solid)
        reject_input_output_alias(options.input, options.output);
    if (std::filesystem::exists(options.output) &&
        !std::filesystem::is_regular_file(options.output))
        throw std::invalid_argument("--output must name a file, not a directory");

    DynamicPrintConfig project_config;
    Model model = load_model_and_project_config(options, project_config);
    validate_minimal_model(model);
    if (options.validate_solid) {
        std::size_t model_parts = 0;
        std::size_t facets = 0;
        double volume = 0.0;
        for (Slic3r::ModelObject *object : model.objects) {
            for (ModelVolume *model_volume : object->volumes) {
                if (!model_volume->is_model_part())
                    continue;
                ++model_parts;
                const Slic3r::TriangleMesh &mesh = model_volume->mesh();
                if (mesh.empty())
                    throw std::runtime_error("support STL contains an empty mesh");
                const double part_volume =
                    std::abs(static_cast<double>(mesh.stats().volume));
                if (!mesh.stats().manifold())
                    throw std::runtime_error(
                        "support STL is not manifold after PrusaSlicer import repair");
                if (!std::isfinite(part_volume) || part_volume <= 0.0 ||
                    mesh.has_zero_volume())
                    throw std::runtime_error(
                        "support STL has no printable positive-volume solid");
                facets += mesh.facets_count();
                volume += part_volume;
            }
        }
        if (model_parts == 0 || facets == 0 || !std::isfinite(volume) || volume <= 0.0)
            throw std::runtime_error("support STL contains no printable model volume");
        std::cout << "printable support solid: " << facets << " facets, "
                  << volume << " mm^3\n";
        return EXIT_SUCCESS;
    }
    if (options.support_paint)
        apply_support_paint(model, *options.support_paint);
    DynamicPrintConfig config = make_config(options, project_config);

    Print print;
    attach_progress(print, options.quiet);
    for (Slic3r::ModelObject *object : model.objects)
        print.auto_assign_extruders(object);

    // Deliberately no arrange(), center_instances_around_point(), ensure_on_bed(),
    // mesh translation, or instance transformation occurs in this adapter.
    print.apply(model, config);
    const std::string validation_error = print.validate();
    if (!validation_error.empty())
        throw std::runtime_error("PrusaSlicer rejected the print: " + validation_error);
    if (print.empty())
        throw std::runtime_error("PrusaSlicer produced an empty print");

    print.process();
    write_json(options, print);
    return EXIT_SUCCESS;
}

} // namespace

int entry(const std::vector<std::string> &arguments)
{
    try {
        return run(parse_options(arguments));
    } catch (const std::invalid_argument &error) {
        std::cerr << "holderpro-organic-engine: " << error.what() << '\n';
        return 2;
    } catch (const std::exception &error) {
        std::cerr << "holderpro-organic-engine: " << error.what() << '\n';
        return 1;
    }
}

#ifdef _WIN32
std::string wide_to_utf8(std::wstring_view value)
{
    if (value.empty())
        return {};
    if (value.size() > static_cast<std::size_t>(std::numeric_limits<int>::max()))
        throw std::runtime_error("command-line argument is too long");
    const int source_size = static_cast<int>(value.size());
    const int output_size = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), source_size,
        nullptr, 0, nullptr, nullptr);
    if (output_size <= 0)
        throw std::runtime_error("command line contains invalid Unicode");
    std::string output(static_cast<std::size_t>(output_size), '\0');
    if (WideCharToMultiByte(
            CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), source_size,
            output.data(), output_size, nullptr, nullptr) != output_size)
        throw std::runtime_error("could not convert command line to UTF-8");
    return output;
}

int wmain(int argc, wchar_t **argv)
{
    try {
        std::vector<std::string> arguments;
        arguments.reserve(static_cast<std::size_t>(argc));
        for (int index = 0; index < argc; ++index)
            arguments.push_back(wide_to_utf8(argv[index]));
        return entry(arguments);
    } catch (const std::exception &error) {
        std::cerr << "holderpro-organic-engine: " << error.what() << '\n';
        return 1;
    }
}
#else
int main(int argc, char **argv)
{
    std::vector<std::string> arguments;
    arguments.reserve(static_cast<std::size_t>(argc));
    for (int index = 0; index < argc; ++index)
        arguments.emplace_back(argv[index]);
    return entry(arguments);
}
#endif
