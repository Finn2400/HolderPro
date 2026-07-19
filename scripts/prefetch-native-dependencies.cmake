# SPDX-License-Identifier: AGPL-3.0-or-later

cmake_minimum_required(VERSION 3.24)

if(NOT DEFINED HOLDERPRO_DEP_DOWNLOAD_DIR OR
   "${HOLDERPRO_DEP_DOWNLOAD_DIR}" STREQUAL "")
    message(FATAL_ERROR "HOLDERPRO_DEP_DOWNLOAD_DIR is required")
endif()

# PrusaSlicer's Windows dependency recipe uses the GMP binaries stored in its
# pinned source tree. Unix builds download the exact upstream source archive.
if(WIN32)
    return()
endif()

set(_gmp_name "gmp-6.2.1.tar.bz2")
set(_gmp_sha256
    "eae9326beb4158c386e39a356818031bd28f3124cf915f8c5b1dc4c7a36b4d7c")
set(_gmp_directory "${HOLDERPRO_DEP_DOWNLOAD_DIR}/GMP")
set(_gmp_destination "${_gmp_directory}/${_gmp_name}")
string(RANDOM LENGTH 16 ALPHABET 0123456789abcdef _partial_token)
set(_gmp_partial "${_gmp_destination}.partial-${_partial_token}")
set(_gmp_urls
    "https://ftp.gnu.org/gnu/gmp/gmp-6.2.1.tar.bz2"
    "https://ftpmirror.gnu.org/gmp/gmp-6.2.1.tar.bz2"
    "https://gmplib.org/download/gmp/gmp-6.2.1.tar.bz2")

file(MAKE_DIRECTORY "${_gmp_directory}")

if(EXISTS "${_gmp_destination}")
    file(SHA256 "${_gmp_destination}" _cached_sha256)
    if(_cached_sha256 STREQUAL _gmp_sha256)
        message(STATUS "Using verified cached GMP ${_gmp_name}")
        return()
    endif()
    message(WARNING "Discarding cached GMP archive with the wrong SHA-256")
    file(REMOVE "${_gmp_destination}")
endif()

foreach(_gmp_url IN LISTS _gmp_urls)
    file(REMOVE "${_gmp_partial}")
    message(STATUS "Fetching pinned GMP source from ${_gmp_url}")
    file(DOWNLOAD
        "${_gmp_url}"
        "${_gmp_partial}"
        STATUS _download_status
        TLS_VERIFY ON
        TIMEOUT 180
        INACTIVITY_TIMEOUT 45)
    list(GET _download_status 0 _download_code)
    list(GET _download_status 1 _download_message)
    if(_download_code EQUAL 0)
        file(SHA256 "${_gmp_partial}" _downloaded_sha256)
        if(_downloaded_sha256 STREQUAL _gmp_sha256)
            # Rename only after verification, so ExternalProject never sees a
            # partial or untrusted archive in its shared download cache.
            file(RENAME "${_gmp_partial}" "${_gmp_destination}")
            message(STATUS "Verified GMP ${_gmp_name}")
            return()
        endif()
        message(WARNING
            "GMP source from ${_gmp_url} had SHA-256 ${_downloaded_sha256}; "
            "expected ${_gmp_sha256}")
    else()
        message(WARNING
            "Could not fetch GMP from ${_gmp_url}: "
            "${_download_code} ${_download_message}")
    endif()
endforeach()

file(REMOVE "${_gmp_partial}")
message(FATAL_ERROR
    "Could not fetch verified GMP ${_gmp_name} from any pinned HTTPS source")
