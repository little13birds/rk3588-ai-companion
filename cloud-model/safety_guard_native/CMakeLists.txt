cmake_minimum_required(VERSION 3.16)
project(cloud_model_safety_guard_native LANGUAGES CXX)

set(RKNN_INCLUDE "$ENV{HOME}/Desktop/image_cropping/3rdparty/rknpu2/include"
    CACHE PATH "Directory containing rknn_api.h")

find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)

add_library(safety_rknn SHARED
    safety_rknn_lib.cpp
    rknn_yolo.cpp
)

target_compile_features(safety_rknn PRIVATE cxx_std_17)
target_compile_options(safety_rknn PRIVATE -O2 -Wall -Wextra)
target_include_directories(safety_rknn PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${RKNN_INCLUDE}
    ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(safety_rknn PRIVATE rknnrt ${OpenCV_LIBRARIES} pthread)

set_target_properties(safety_rknn PROPERTIES
    OUTPUT_NAME "safety_rknn"
    BUILD_RPATH "$ORIGIN"
    INSTALL_RPATH "$ORIGIN"
)
