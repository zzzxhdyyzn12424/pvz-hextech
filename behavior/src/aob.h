// ============================================================================
//  aob.h / aob.cpp  ——  特征码扫描
// ============================================================================

#pragma once
#include <cstdint>
#include <cstddef>

namespace Aob {

// sig  = "55 8B EC 83 ? ? ? E8"
// mask = "xxx??xx"   x = 精确匹配, ? = 通配
void* FindFirst(const char* sig, void* start, size_t len);

// 统计命中数量（用来校验特征码唯一性）
int   CountAll(const char* sig, void* start, size_t len);

// 在整个模块镜像里找（内部会跳过不可执行页）。返回主模块基址，失败 nullptr。
//   sig        —— 特征码
//   hitCount   —— 输出命中数
void* FindInImage(const char* sig, int* hitCount);

// App 单例。当前实现返回 nullptr，改由钩子运行时从 board 反查（见 tangshi.cpp）。
void* FindAppInstance();

} // namespace Aob
