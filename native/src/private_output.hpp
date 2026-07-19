// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Cross-platform primitives for staging model-derived native output without a
// create-then-tighten permissions window.

#pragma once

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

namespace holderpro::native {

#ifdef _WIN32
class OwnerOnlySecurityAttributes {
public:
    OwnerOnlySecurityAttributes()
    {
        HANDLE token = nullptr;
        if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token))
            throw_last_error("cannot open the current process token");

        DWORD size = 0;
        GetTokenInformation(token, TokenUser, nullptr, 0, &size);
        const DWORD size_error = GetLastError();
        if (size_error != ERROR_INSUFFICIENT_BUFFER || size == 0) {
            CloseHandle(token);
            throw std::system_error(
                static_cast<int>(size_error), std::system_category(),
                "cannot size the current user security identifier");
        }
        token_user_.resize(size);
        if (!GetTokenInformation(token, TokenUser, token_user_.data(), size, &size)) {
            const DWORD error = GetLastError();
            CloseHandle(token);
            throw std::system_error(
                static_cast<int>(error), std::system_category(),
                "cannot read the current user security identifier");
        }
        CloseHandle(token);

        const auto *token_user =
            reinterpret_cast<const TOKEN_USER *>(token_user_.data());
        PSID user_sid = token_user->User.Sid;
        if (!IsValidSid(user_sid))
            throw std::runtime_error("the current user security identifier is invalid");

        const DWORD sid_size = GetLengthSid(user_sid);
        const DWORD acl_size = static_cast<DWORD>(
            sizeof(ACL) + sizeof(ACCESS_ALLOWED_ACE) - sizeof(DWORD) + sid_size);
        acl_.resize(acl_size);
        auto *acl = reinterpret_cast<ACL *>(acl_.data());
        if (!InitializeAcl(acl, acl_size, ACL_REVISION))
            throw_last_error("cannot initialize an owner-only access list");
        if (!AddAccessAllowedAceEx(
                acl, ACL_REVISION, 0, GENERIC_ALL, user_sid))
            throw_last_error("cannot grant the current user private access");

        if (!InitializeSecurityDescriptor(
                &descriptor_, SECURITY_DESCRIPTOR_REVISION))
            throw_last_error("cannot initialize a private security descriptor");
        if (!SetSecurityDescriptorOwner(&descriptor_, user_sid, FALSE))
            throw_last_error("cannot set the private output owner");
        if (!SetSecurityDescriptorDacl(&descriptor_, TRUE, acl, FALSE))
            throw_last_error("cannot set an owner-only protected access list");
        if (!SetSecurityDescriptorControl(
                &descriptor_, SE_DACL_PROTECTED, SE_DACL_PROTECTED))
            throw_last_error("cannot protect the owner-only access list");

        attributes_.nLength = sizeof(attributes_);
        attributes_.lpSecurityDescriptor = &descriptor_;
        attributes_.bInheritHandle = FALSE;
    }

    SECURITY_ATTRIBUTES *get() { return &attributes_; }

private:
    [[noreturn]] static void throw_last_error(const char *message)
    {
        throw std::system_error(
            static_cast<int>(GetLastError()), std::system_category(), message);
    }

    std::vector<unsigned char> token_user_;
    std::vector<unsigned char> acl_;
    SECURITY_DESCRIPTOR descriptor_{};
    SECURITY_ATTRIBUTES attributes_{};
};
#endif

inline bool create_owner_only_directory(
    const std::filesystem::path &path, std::error_code &error)
{
#ifdef _WIN32
    OwnerOnlySecurityAttributes security;
    if (CreateDirectoryW(path.c_str(), security.get())) {
        error.clear();
        return true;
    }
    error = std::error_code(
        static_cast<int>(GetLastError()), std::system_category());
    return false;
#else
    if (::mkdir(path.c_str(), S_IRWXU) == 0) {
        // A restrictive umask may remove owner permissions. Adding only owner
        // permissions after creation cannot expose the directory to anyone
        // else, and makes the exact invariant independent of the caller's
        // process-wide umask.
        if (::chmod(path.c_str(), S_IRWXU) != 0) {
            error = std::error_code(errno, std::generic_category());
            ::rmdir(path.c_str());
            return false;
        }
        error.clear();
        return true;
    }
    error = std::error_code(errno, std::generic_category());
    return false;
#endif
}

inline void reserve_private_output_file(const std::filesystem::path &path)
{
#ifdef _WIN32
    OwnerOnlySecurityAttributes security;
    const HANDLE handle = CreateFileW(
        path.c_str(), GENERIC_WRITE, 0, security.get(), CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        throw std::system_error(
            static_cast<int>(GetLastError()), std::system_category(),
            "cannot exclusively create private output");
    if (!CloseHandle(handle)) {
        const DWORD error = GetLastError();
        DeleteFileW(path.c_str());
        throw std::system_error(
            static_cast<int>(error), std::system_category(),
            "cannot close private output reservation");
    }
#else
    int flags = O_WRONLY | O_CREAT | O_EXCL;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int descriptor = ::open(path.c_str(), flags, S_IRUSR | S_IWUSR);
    if (descriptor < 0)
        throw std::system_error(
            errno, std::generic_category(),
            "cannot exclusively create private output");
    if (::fchmod(descriptor, S_IRUSR | S_IWUSR) != 0) {
        const int error = errno;
        ::close(descriptor);
        ::unlink(path.c_str());
        throw std::system_error(
            error, std::generic_category(),
            "cannot secure private output permissions");
    }
    if (::close(descriptor) != 0) {
        const int error = errno;
        ::unlink(path.c_str());
        throw std::system_error(
            error, std::generic_category(),
            "cannot close private output reservation");
    }
#endif
}

class PrivateTemporaryDirectory {
public:
    explicit PrivateTemporaryDirectory(const std::filesystem::path &destination)
    {
        const std::filesystem::path parent = destination.parent_path().empty()
            ? std::filesystem::path(".") : destination.parent_path();
        static std::atomic<std::uint64_t> counter{0};
        std::random_device random;
        for (std::uint64_t attempt = 0; attempt < 128; ++attempt) {
            const auto clock = static_cast<std::uint64_t>(
                std::chrono::steady_clock::now().time_since_epoch().count());
            const std::uint64_t token =
                (static_cast<std::uint64_t>(random()) << 32U) ^
                static_cast<std::uint64_t>(random()) ^ clock ^
                counter.fetch_add(1, std::memory_order_relaxed) ^ attempt;
            std::ostringstream token_text;
            token_text << ".holderpro-" << std::hex << token;
            std::filesystem::path name(".");
            name += destination.filename();
            name += std::filesystem::path(token_text.str());
            const std::filesystem::path candidate = parent / name;
            std::error_code error;
            if (create_owner_only_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
            if (error && error != std::errc::file_exists)
                throw std::system_error(
                    error, "cannot create private temporary output directory");
        }
        throw std::runtime_error(
            "cannot allocate a unique private temporary output directory");
    }

    ~PrivateTemporaryDirectory()
    {
        std::error_code ignored;
        std::filesystem::remove_all(path_, ignored);
    }

    PrivateTemporaryDirectory(const PrivateTemporaryDirectory &) = delete;
    PrivateTemporaryDirectory &operator=(const PrivateTemporaryDirectory &) = delete;

    const std::filesystem::path &path() const { return path_; }

private:
    std::filesystem::path path_;
};

} // namespace holderpro::native
