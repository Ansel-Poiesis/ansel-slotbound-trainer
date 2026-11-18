# Ansel Slotbound Trainer · 转运军团开源修改器

**Windows x64 · v4.4.1 · MIT · 简体中文界面**

适用于《转运军团》Slotbound 的独立开源修改器，作者 **Ansel**。
源码、图标源文件、测试和构建配置均公开；游戏需自行合法获取。

[下载 Windows 发行版](https://github.com/Ansel-Poiesis/ansel-slotbound-trainer/releases/latest)
 · [问题反馈](https://github.com/Ansel-Poiesis/ansel-slotbound-trainer/issues)
 · [English](#english)

## 能修改什么

- **五档抽卡概率**：普通、高级、稀有、传说、棱镜分别设置百分比，支持归一化及稀有优先、传说优先、全传说快捷配比。用于中奖后新招募单位的品质；盘面中奖率单独控制。
- **抽奖与经济**：持续抽奖辅助、下一抽必中、六类兵种图标权重、每波旋转和免费额度、金币、基地生命、免金币、未中奖返还、清波基础奖励。
- **快捷修改**：一键补给（金币 +10000、免费旋转 +20）、单项补给、稀有优先、全传说、部队两倍。
- **单位与成长**：现有和新招募单位的攻击、生命、攻速倍率，吸收成长和战斗速度。
- **局外存档**：源质、解锁、成就统计；自动备份、原子写入、恢复。
- **高级工具**：48 项参数、数值首扫／再扫／改值／锁定，关闭时恢复仍被覆盖的值。

六类兵种使用本项目原创图标，界面显示战士、弓手、枪兵、法师、决斗者、骑兵，不用字母代替图标。图标矢量源和生成器见 [assets](assets) 与 [tools/generate_icons.py](tools/generate_icons.py)。

![原创兵种图标](assets/classes-preview.png)

## 使用

1. 从 Releases 下载 ZIP，解压后启动 `AnselSlotboundTrainer.exe`。
2. 启动游戏并进入一局；修改器自动连接正在运行的 `Slotbound.exe`。各项默认关闭，先输入数值再开启。
3. 局外存档功能在关闭游戏后使用。如未发现安装目录，点界面上方 **游戏目录…**，选择包含 `Slotbound.exe` 和 `lua51.dll` 的文件夹。支持 Steam 库自动发现和已运行进程目录发现。
4. 无需安装 Python。源码用户可用 Python 3.11+ x64（带 Tk）运行 `python -X utf8 trainer_gui.py`，运行依赖仅标准库。

小键盘需开启 Num Lock，热键在修改器或游戏处于前台时生效。

| 按键 | 操作 |
| --- | --- |
| Num0 | 一键补给：金币 +10000，免费旋转 +20 |
| Num1 / Num2 | 持续抽奖辅助 / 下一抽必中 |
| Num3 / Num4 / Num5 | 增加免费旋转 / 金币 / 设置基地生命 |
| Num6 / Num7 / Num8 / Num9 | 免金币 / 招募品质 / 清波基础奖励 / 战斗速度 |
| Ctrl+Shift+Home | 暂停或恢复热键 |

免费额度属于总旋转数；添加免费旋转会同时增加总数。每波额度只在启用、改值或进入新波备战时补充，不会每帧补满。重复点击部队两倍仍为两倍。关闭倍率移除修改器覆盖，已获得和已消耗的游戏结果保留。3 倍战斗速度受原生无尽模式解锁限制。

## 适配与验证

当前针对 **Slotbound 1.0.0.7 / Windows x64 / LuaJIT GC64** 开发，游戏 Steam AppID 为 **4459590**。其他版本及 Steam 发行构建的实机适配需单独核验。

公开测试覆盖控制器、概率输入、恢复、图标、路径和独立合成 LuaJIT 状态。可选集成测试读取用户提供的游戏 DLL，在隔离状态和临时存档运行；不会启动游戏、操作桌面或使用正常存档。它们不等同于目标游戏的完整实机验收。当前版本未新增桌面窗口视觉验收。

```powershell
# 无游戏即可运行；集成用例会明确跳过
python -X utf8 -m unittest discover -v
# 打包程序也提供无窗口检查，并向 JSON 和同名日志写结果
.\AnselSlotboundTrainer.exe --verify-runtime --result .\checks.json
# 可选：仅在自己电脑上指定游戏目录；不会把游戏文件加入仓库或包
$env:SLOTBOUND_GAME_DIR = 'D:/Games/Slotbound'
python -X utf8 -m unittest discover -v
```

路径配置保存在 `%LOCALAPPDATA%/Ansel/SlotboundTrainer/settings.json`。
存档位于 `%APPDATA%/slotbound_full/save_fallback.dat`，备份为 `.sbmod_bak` 后缀。
恢复会回到首次修改前的进度，操作前核对备份时间。

## 从源码构建

使用 Windows x64、Python 3.11（带 Tk）。在仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -v
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm SlotboundModifier.spec
```

输出 `dist/AnselSlotboundTrainer.exe`。已提交 PNG/ICO，无需重新生成图标；编辑图标后运行 `python tools/generate_icons.py`。Release ZIP 应含程序、README、LICENSE、第三方声明和许可证；校验发行页附带的 SHA-256。

本项目的完整现用产品源码包含内存修改、扫描、热键、存档、原创图标和测试。开发时的旧游戏档案提取／反编译工具不属于运行依赖，不随开源产品分发。

## 许可和收录

本项目源码和原创图标使用 [MIT](LICENSE)。第三方组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。游戏及第三方品牌权利不变。

本项目独立维护。GitHub 发布、3DM 网页审核和 FLiNG 客户端收录是独立状态；当前未宣称获得 FLiNG／3DM 官方合作或合集接收。收录申请以站方实际回执为准。

## English

**Ansel Slotbound Trainer** is an independent, MIT-licensed Windows x64 trainer
for **Slotbound (转运军团)**, Steam AppID **4459590**. It offers custom recruitment
probabilities for Normal / Advanced / Rare / Legendary / Prism units, original
class icons, one-click supplies, unit multipliers, growth, save editing with
backup and restore, hotkeys and a value scanner. The current interface is Chinese.

Download the Windows ZIP from [Releases](https://github.com/Ansel-Poiesis/ansel-slotbound-trainer/releases).
Start your game and a run, then launch the trainer. All toggles start disabled.
For save editing, close the game and select your installation using **游戏目录…**.
The executable needs no Python installation. Source execution needs only Python
3.11+ x64 with Tk. Build dependencies and Windows build commands are above.

Target: game **1.0.0.7**, LuaJIT GC64. Other builds require separate verification.
Tests use controllers and synthetic data; optional native integration loads the
user's own game DLL in isolated states. No proprietary game files, decompiled
code, game art, player saves or LJD are included. This is not an official FLiNG,
3DM or Optima Arch product. Collection acceptance must be confirmed separately.
