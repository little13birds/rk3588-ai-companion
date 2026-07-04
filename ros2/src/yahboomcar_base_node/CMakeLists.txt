cmake_minimum_required(VERSION 3.8)
project(yahboomcar_base_node)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)

find_package(turtlesim REQUIRED)
find_package(rclcpp REQUIRED)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()


add_executable(base_node_R2 src/base_node_R2.cpp)
add_executable(base_node_X3 src/base_node_X3.cpp)
add_executable(base_node_x1 src/base_node_x1.cpp)


target_include_directories(base_node_R2 PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
  
target_compile_features(base_node_R2 PUBLIC c_std_99 cxx_std_17)  # Require C99 and C++17
ament_target_dependencies(base_node_X3 rclcpp tf2 tf2_ros nav_msgs geometry_msgs turtlesim)
ament_target_dependencies(base_node_x1 rclcpp tf2 tf2_ros nav_msgs geometry_msgs turtlesim)
ament_target_dependencies(
  base_node_R2
  "rclcpp"
  "geometry_msgs"
  "turtlesim"
  "tf2_ros"
  "tf2"
  "nav_msgs"
)

install(TARGETS base_node_R2 base_node_X3 base_node_x1
  DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
