# SPDX-License-Identifier: LicenseRef-CSSL-1.0

add_executable(test_fd_channel
  test_fd_channel.c
  ../fd_channel.c
  ../../../openair1/PHY/TOOLS/dfts_load.c
)
target_include_directories(test_fd_channel PRIVATE ..)
target_link_libraries(test_fd_channel PRIVATE minimal_lib shlib_loader SIMU m)
add_dependencies(tests test_fd_channel)
add_dependencies(test_fd_channel dfts)
add_test(NAME test_fd_channel COMMAND ./test_fd_channel)
set_tests_properties(test_fd_channel PROPERTIES
  ENVIRONMENT "LD_LIBRARY_PATH=${CMAKE_BINARY_DIR}"
)
