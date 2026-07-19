cmake_minimum_required(VERSION 3.24)

if(NOT DEFINED HOLDERPRO_ENGINE OR NOT EXISTS "${HOLDERPRO_ENGINE}")
    message(FATAL_ERROR "HOLDERPRO_ENGINE must name the built native engine")
endif()
if(NOT DEFINED HOLDERPRO_TEST_DIR)
    message(FATAL_ERROR "HOLDERPRO_TEST_DIR is required")
endif()

function(expect_alias_rejected input output description)
    execute_process(
        COMMAND "${HOLDERPRO_ENGINE}" --input "${input}" --output "${output}"
        RESULT_VARIABLE result
        OUTPUT_VARIABLE stdout
        ERROR_VARIABLE stderr)
    if(NOT result EQUAL 2)
        message(FATAL_ERROR
            "${description} was not rejected as a usage error (exit ${result}).\n"
            "stdout: ${stdout}\nstderr: ${stderr}")
    endif()
    string(CONCAT combined "${stdout}" "${stderr}")
    if(NOT combined MATCHES "--output must not overwrite --input")
        message(FATAL_ERROR
            "${description} returned the wrong diagnostic.\n${combined}")
    endif()
endfunction()

file(REMOVE_RECURSE "${HOLDERPRO_TEST_DIR}")
file(MAKE_DIRECTORY "${HOLDERPRO_TEST_DIR}")

# A Unicode-named hard link proves the check uses filesystem identity rather
# than spelling alone. Hard links are supported on all HolderPro target filesystems.
set(input "${HOLDERPRO_TEST_DIR}/Mödel-支柱.stl")
set(hardlink "${HOLDERPRO_TEST_DIR}/different-ссылка.json")
file(WRITE "${input}" "HolderPro native alias-safety fixture\n")
file(CREATE_LINK "${input}" "${hardlink}" RESULT link_result)
if(NOT link_result STREQUAL "0")
    message(FATAL_ERROR "could not create hard-link test fixture: ${link_result}")
endif()

file(SHA256 "${input}" hash_before)
expect_alias_rejected("${input}" "${hardlink}" "Unicode hard-link alias")
file(SHA256 "${input}" hash_after)
if(NOT hash_before STREQUAL hash_after)
    message(FATAL_ERROR "the adapter modified its input while rejecting an alias")
endif()

# Exercise differently-cased names when the host filesystem treats them as
# aliases (Windows and the default macOS filesystem). Case-sensitive hosts skip it.
set(case_input "${HOLDERPRO_TEST_DIR}/CaseAlias.stl")
set(case_output "${HOLDERPRO_TEST_DIR}/casealias.stl")
file(WRITE "${case_input}" "HolderPro native case-alias fixture\n")
if(EXISTS "${case_output}")
    expect_alias_rejected("${case_input}" "${case_output}" "case-only alias")
endif()
