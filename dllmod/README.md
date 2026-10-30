# dllmod —— 进程内版海克斯（改本体路线）

> 目标：让海克斯版以**改版**的形态存在，而不是「游戏 + 一个外挂程序」。
> 玩家把两个文件丢进游戏目录，双击游戏，海克斯就在里面。
> 最后更新：2026-10-30

---

## 现在的状态

**里程碑 1 已通过**：代理 DLL 能被游戏加载，代码在游戏进程内跑起来，能读到 `gLawnApp`。

```
=== hextech in-process (milestone 1) ===
module base = 65750000
[t=   0] app=03264ee8 board=00000000 scene=0 level=0 wave=0/0 sun=0 plants=0 zombies=0
```

`board=0` 是对的——主菜单里还没有 Board。进关卡后应该出现非零 board、太阳数、植物数。

还没做的：词条引擎移植。见文末路线图。

---

## 为什么代理 `wsock32.dll`

PVZ 1.0.0.1051 的静态导入表只有这些：

```
KERNEL32 / USER32 / WININET / WINMM / WSOCK32 / GDI42 / ADVAPI32 / SHELL32 / ole32 / OLEAUT32
```

选代理目标的三个条件，`wsock32` 全中：

| 条件 | wsock32 | 说明 |
|---|---|---|
| 在导入表里 | ✅ | 游戏启动就会加载它 |
| 不在 `KnownDLLs` | ✅ | 否则永远从 System32 加载，代理无效 |
| 导出少且全具名 | ✅ | 75 条，没有「仅序号」的导出 |

`version.dll` 是网上教程里最常见的代理目标，但**这个游戏不导入它**，放进去也不会被加载。

### 坑：PVZ 是按序号导入 WSOCK32 的

```
#12 ioctlsocket   #16 recv      #116 WSACleanup  #18 select
#9  htons         #111 WSAGetLastError  #23 socket  #52 gethostbyname
#10 inet_addr     #3 closesocket  #19 send      #115 WSAStartup
#151 __WSAFDIsSet #4  connect
```

只导出名字不带序号 → 游戏启动直接 `0xC0000138 STATUS_ORDINAL_NOT_FOUND`。
所以 `gen_proxy.py` 生成的 `.def` 里每条都带原始序号：

```
accept = wsock32_orig.accept @1
```

---

## 怎么工作

```
游戏目录/
  PlantsVsZombies.exe      ← 一个字节都不改
  wsock32.dll              ← 我们造的代理（230 KB）
  wsock32_orig.dll         ← 真正的系统 wsock32 改名而来（16 KB）
```

加载顺序：

1. 游戏启动，加载 `WSOCK32.dll` → 命中游戏目录里我们的那份
2. 我们的导出表是 75 条 **forwarder**（`名字 = wsock32_orig.名字`），
   加载器自动去加载 `wsock32_orig.dll` 并解析——**没有一行转发代码**
3. 我们的 `DllMain` 起一个线程，跑海克斯逻辑

对外表现：**双击游戏，海克斯就在里面**。没有外部程序、没有第二个窗口、没有注入器。

卸载：删掉这两个文件即可，游戏恢复原版。

---

## 编译

```bat
dllmod\build.bat
```

依赖：
- MinGW 32 位工具链 `C:\pvztc\mingw32`（`i686-w64-mingw32-g++`，GCC 16.2.0）
- 项目自带的 `.venv`（生成 `.def` 用）

产物在 `dllmod\out\`。

> ⚠️ 工具链的 DLL 必须在 `PATH` 里，否则 `gcc` 会**静默失败**（rc=1 且没有任何输出）。
> 还要加 `-fno-use-linker-plugin`，这台机器没有 `liblto_plugin.dll`。

---

## 文件

| 文件 | 作用 |
|---|---|
| `gen_proxy.py` | 读 `SysWOW64\wsock32.dll` 的导出表，生成带序号的 forwarder `.def` |
| `src/proxy.cpp` | `DllMain` + 探针线程（里程碑 1） |
| `src/pvz.h` | 游戏内存布局 + 进程内访问器（`Rd` / `Wr` / `Ptr` / `Grab`） |
| `src/log.h` | 落盘日志，写 exe 同目录的 `hextech_dll.log` |
| `build.bat` | 三步：生成 `.def` → 复制真 dll → 编译 |

---

## 路线图

- [x] **M1 代理层**：DLL 被加载、线程能跑、能读 `gLawnApp`
- [ ] **M2 场景读取**：进关卡后正确读出 Board / 太阳 / 植物数组 / 僵尸数组
- [ ] **M3 词条引擎**：把 `tools/hextech_core.py` 的 48 条词条移植进 C++
- [ ] **M4 每帧驱动**：挂到游戏自己的 Update 里，不再是独立线程轮询
- [ ] **M5 界面**：用游戏自己的绘制画词条面板，去掉外部覆盖层

### M3 的工作量说明

`tools/hextech_core.py` 有 2403 行，但其中很大一块是 ctypes 管道
（`ReadProcessMemory` 包装、`struct` 打包、进程扫描）。进程内这些全部消失，
直接解引用即可。真正的逻辑估计在 1200～1500 行 C++。

好处也明显：外置版只能「改数值 + 范围伤害借壳」，进程内可以挂到植物更新里，
做真正的新行为；而且没有每帧过内核的开销。
