    .text

# 每帧回调：保存现场 -> 调 C++ -> 还原 -> 跳回原函数体
# MinGW 32 位：C 符号带前导下划线
    .globl _hx_detour
_hx_detour:
    pushf
    pushal
    call _hx_OnFrame
    popal
    popf
    movl _hx_real_body, %eax
    jmp *%eax
