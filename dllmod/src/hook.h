// ============================================================================
//  hook.h —— 极简内联钩子
//
//  我们只钩一个地方：0x539D30。
//
//  为什么是它：
//    Board::Update 在 0x415D40，但它开头是带 SEH 的 prologue
//    （push -1 / push handler / mov fs:[0],esp ...），直接改前 5 字节要连
//    SEH 记录一起搬，风险高。
//    而 0x415D7A 处 `call 0x539D30` 调到的 0x539D30 整个函数只有一条
//    `jmp 0x537F50`（5 字节 thunk）—— 换掉这条 jmp 就行，**一条指令都不用搬**。
//
//    0x539D30 在 Board::Update 最开头、无条件调用，所以 = 每帧一次。
//    而 0x415D99 那个 `cmp [mApp+0x7F8], 0x2B` 的模式门在它**后面**，不受影响。
// ============================================================================
#pragma once

#include <cstdint>

namespace hx {

// 游戏每帧会走到的 thunk
constexpr uintptr_t kFrameThunk = 0x00539D30;
// 它的真实函数体
constexpr uintptr_t kFrameBody = 0x00537F50;

// 装钩子。成功返回 true；失败时调用方应退回轮询模式。
bool InstallFrameHook();

}  // namespace hx
