// SPDX-License-Identifier: AGPL-3.0-or-later

#include "private_output.hpp"

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/stat.h>
#endif

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

namespace {

[[noreturn]] void fail(const std::string &message)
{
    throw std::runtime_error(message);
}

#ifdef _WIN32
void require_owner_only(const std::filesystem::path &path)
{
    constexpr SECURITY_INFORMATION information =
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
    DWORD size = 0;
    GetFileSecurityW(path.c_str(), information, nullptr, 0, &size);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || size == 0)
        fail("could not size the output security descriptor");
    std::vector<unsigned char> storage(size);
    auto *descriptor = reinterpret_cast<PSECURITY_DESCRIPTOR>(storage.data());
    if (!GetFileSecurityW(path.c_str(), information, descriptor, size, &size))
        fail("could not read the output security descriptor");

    SECURITY_DESCRIPTOR_CONTROL control = 0;
    DWORD revision = 0;
    if (!GetSecurityDescriptorControl(descriptor, &control, &revision) ||
        (control & SE_DACL_PROTECTED) == 0)
        fail("the output DACL is not protected from inheritance");

    PSID owner = nullptr;
    BOOL owner_defaulted = FALSE;
    if (!GetSecurityDescriptorOwner(descriptor, &owner, &owner_defaulted) ||
        owner == nullptr)
        fail("the output has no owner SID");

    PACL dacl = nullptr;
    BOOL dacl_present = FALSE;
    BOOL dacl_defaulted = FALSE;
    if (!GetSecurityDescriptorDacl(
            descriptor, &dacl_present, &dacl, &dacl_defaulted) ||
        !dacl_present || dacl == nullptr || dacl->AceCount != 1)
        fail("the output does not have exactly one explicit access rule");

    void *raw_ace = nullptr;
    if (!GetAce(dacl, 0, &raw_ace))
        fail("the output access rule could not be read");
    const auto *ace = static_cast<const ACCESS_ALLOWED_ACE *>(raw_ace);
    if (ace->Header.AceType != ACCESS_ALLOWED_ACE_TYPE ||
        !EqualSid(owner, const_cast<DWORD *>(&ace->SidStart)))
        fail("the output access rule is not restricted to its owner");
}
#else
void require_mode(const std::filesystem::path &path, mode_t expected)
{
    struct stat status {};
    if (::lstat(path.c_str(), &status) != 0)
        throw std::system_error(
            errno, std::generic_category(), "could not inspect private output");
    if ((status.st_mode & 0777) != expected)
        fail(
            "private output mode was " +
            std::to_string(static_cast<unsigned>(status.st_mode & 0777)) +
            ", expected " + std::to_string(static_cast<unsigned>(expected)));
}
#endif

} // namespace

int main(int argc, char **argv)
{
    try {
        if (argc != 2)
            fail("expected one temporary test-directory argument");
        const std::filesystem::path root =
            std::filesystem::u8path(argv[1]);
        std::error_code cleanup_error;
        std::filesystem::remove_all(root, cleanup_error);
        std::filesystem::create_directories(root);

#ifndef _WIN32
        const mode_t previous_umask = ::umask(0);
#endif
        std::filesystem::path temporary_path;
        {
            holderpro::native::PrivateTemporaryDirectory directory(
                root / "finished-support-layers.json");
            temporary_path = directory.path();
#ifdef _WIN32
            require_owner_only(temporary_path);
#else
            require_mode(temporary_path, 0700);
#endif

            const std::filesystem::path output =
                temporary_path / "support-layers.json";
            holderpro::native::reserve_private_output_file(output);
#ifdef _WIN32
            require_owner_only(output);
#else
            require_mode(output, 0600);
#endif

            bool rejected_existing = false;
            try {
                holderpro::native::reserve_private_output_file(output);
            } catch (const std::system_error &) {
                rejected_existing = true;
            }
            if (!rejected_existing)
                fail("exclusive private-output creation accepted an existing path");
        }
#ifndef _WIN32
        ::umask(previous_umask);
#endif
        if (std::filesystem::exists(temporary_path))
            fail("private temporary directory was not removed");
        std::filesystem::remove_all(root, cleanup_error);
        std::cout << "private native output staging OK\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "private-output-test: " << error.what() << '\n';
        return 1;
    }
}
