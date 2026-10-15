# MolScene

**调用你已有的 PyMOL，生成可复现的分子结构图与结构分析记录。**

[English](README.md) · [样例与数据来源](examples/README.md) · [测试与验证](docs/TESTING.md) · [接口分析](docs/INTERNALS.md)

MolScene 将本地 PDB/mmCIF 文件转换为光线追踪图片、可编辑的 PyMOL 会话、重放脚本与 JSON 报告。它适合统一论文或组会中的结构图风格，检查配体周围残基，测量指定原子间距离，并查看相关蛋白的叠合，同时保留生成图片时使用的输入与参数。

| 泛素概览 | HIV 蛋白酶配体口袋 |
| --- | --- |
| ![1UBQ 泛素 cartoon 概览](examples/outputs/overview/scene.png) | ![1HSG 中 MK1 配体及周围蛋白残基](examples/outputs/pocket/scene.png) |

| 分子表面 | PDB/mmCIF 叠合检查 |
| --- | --- |
| ![1UBQ 泛素表面图](examples/outputs/surface/scene.png) | ![1UBQ 的 PDB 与 mmCIF 坐标叠合](examples/outputs/alignment/scene.png) |

## 快速开始

需要 Python 3.10 或更新版本，且**该 Python 环境能够导入 `pymol2`**。MolScene 不附带或自动安装 PyMOL。以下命令从仓库根目录运行；其中 `python` 指你的 PyMOL 环境所用的解释器。

```sh
git clone https://github.com/c-chunhao/molscene.git
cd molscene
python -m molscene doctor
python examples/download_examples.py
python -m molscene render examples/data/1ubq.pdb --output results/overview
```

例如，若 Windows 上已有的 PyMOL 提供 `D:/soft/pymol/python.exe`，可在 PowerShell 中这样调用；请替换为自己的安装路径：

```powershell
& "D:/soft/pymol/python.exe" -m molscene doctor
& "D:/soft/pymol/python.exe" -m molscene render examples/data/1ubq.pdb --output results/overview
```

仓库本身可直接运行。若要从其他目录调用，可选地安装到同一个环境：

```sh
python -m pip install -e .
```

若 `doctor` 无法导入 PyMOL，请切换到已有的 PyMOL Python，或使用独立的[开源 PyMOL 环境](https://github.com/schrodinger/pymol-open-source)。例如：

```sh
conda create -n molscene -c conda-forge python=3.10 pymol-open-source pillow pip
conda activate molscene
python -m pip install -e .
python -m molscene doctor
```

Pillow 用于测试中的图像验证；核心 CLI 使用 Python 标准库与单独安装的 PyMOL。环境兼容性的实测范围见[测试文档](docs/TESTING.md)。

## 常用工作流

```sh
# 默认 cartoon：蛋白按链着色，有机配体显示为 sticks
python -m molscene render examples/data/1ubq.cif --output results/cartoon --width 1600 --height 1200 --dpi 300

# 蛋白表面
python -m molscene render examples/data/1ubq.pdb --style surface --output results/surface

# MK1 配体 5 Å 范围内蛋白原子所在的完整残基
python -m molscene render examples/data/1hsg.pdb --style pocket --ligand "resn MK1" --radius 5 --output results/pocket

# 两端各精确选择一个原子，测量几何距离
python -m molscene measure examples/data/1ubq.pdb --selection-a "chain A and resi 1 and name CA" --selection-b "chain A and resi 2 and name CA" --output results/distance

# 用同一结构的两种格式检查坐标一致性
python -m molscene align examples/data/1ubq.pdb examples/data/1ubq.cif --selection "polymer.protein and name CA" --output results/alignment

# 离线生成固定测试结构并检查后端
python -m molscene selftest --output results/selftest
```

使用 `python -m molscene COMMAND --help` 查看选项。选择表达式采用 [PyMOL 语法](https://pymol.org/pymol-command-ref.html)，应加引号，确保 shell 将整个表达式作为一个参数传入；MolScene 会将其限制在相应的已加载结构对象内。

制图、测距和叠合成功后，输出目录包含：

| 文件 | 用途 |
| --- | --- |
| `scene.png` | 指定像素尺寸的结构图 |
| `scene.pse` | 可继续编辑的 PyMOL 会话 |
| `scene.pml` | 可独立重放的脚本，随附输入文件副本 |
| `report.json` | UTC 时间、工具/后端版本、输入 SHA-256、参数、结构计数、分析结果与产物名称 |

分享重放时，请保留完整输出目录。在新的 PyMOL 会话中进入该目录执行 `@scene.pml`，或直接打开 `scene.pse` 继续编辑。报告用文件名和哈希标识输入，避免公开本机绝对路径。

输出目录须为空。需要明确覆盖并重新生成时，添加 `--overwrite`；它只替换 MolScene 固定产物，不删除整个目录。不存在的输入、空选择、非单原子测距端点、非有限数值及输入/输出冲突等错误会以非零退出码结束。

## 如何理解结果

- **口袋是几何邻域。** MolScene 先找配体指定半径内的蛋白原子，再扩展至完整残基。固定的 1HSG 样例中，`resn MK1` 为 45 个原子；5 Å 范围内有 115 个蛋白原子，扩展后为 33 个蛋白残基、236 个原子。这不等同于氢键判定或结合力预测。
- 测距以 Å 为单位；每个选择须恰好包含一个原子。跨结构比较前，请核对链、残基、原子名及替代构象。
- 叠合调用 PyMOL `cmd.align`，包含序列匹配与离群点迭代剔除。JSON 记录其统计量与参数，适合相关蛋白；RMSD 不是通用相似性评分。上图将同一 1UBQ 的两种文件格式叠合，用于检查可复现性。
- 表面图展示几何表面，不计算静电势；图像清晰也不代表结构模型本身已经验证。

## 验证、范围与参与

2026-10-15，MolScene 的完整 **35 项测试**与 CLI selftest 的 **9 项检查**均在两个本机 PyMOL 后端通过（实现版本 `9b8df7c`）：

| 本机 Windows 环境 | 工具测试 | CLI selftest |
| --- | --- | --- |
| Python 3.10.18 + 已安装的 Incentive PyMOL 3.1.6.1 | 35 通过，0 跳过 | 9/9 通过 |
| Python 3.10.18 + 社区开源 wheel 3.2.0a0，native 3.2.0a | 35 通过，0 跳过 | 9/9 通过 |
| 无 PyMOL 的普通 Python 3.14.3 | 发现 35 项，17 通过、18 跳过 | 需要 PyMOL |

社区 wheel 为预发布版本。本机结果不代表 Linux conda-forge 环境已经验证；该环境须以实际 CI 运行成功为准。[测试文档](docs/TESTING.md) 记录了命令、覆盖范围、耗时与样例图人工检查。

公开样例图使用独立安装的社区开源 wheel（包版本 **3.2.0a0**，native 版本 **3.2.0a**）正常生成。本机已安装发行版在较大尺寸导出时出现评估版标记；[环境与输出说明](docs/TESTING.md#tested-environments-and-output-limits) 区分了 API 测试结果、许可状态与图像质量检查。

制图以蛋白为中心，要求输入包含蛋白原子；`render` 目前不支持只有水或只有 DNA/RNA 的结构。`measure` 可在已加载对象中使用一般原子选择，但每个端点仍须恰好选择一个原子。核心工具对本地文件的操作可以离线完成。只有可选样例下载器会连接 RCSB PDB，并且仅下载三个固定、校验 SHA-256 的公开文件。首版不提供 GUI、核心 CLI 联网抓取、分子对接、轨迹处理或结构预测。

开发与问题反馈见 [CONTRIBUTING.md](CONTRIBUTING.md)。MolScene 自有代码使用 [MIT 许可](LICENSE)。PyMOL 是独立依赖，应遵循所安装发行版自身的许可；[开源版许可见上游](https://github.com/schrodinger/pymol-open-source/blob/master/LICENSE)。MolScene 为独立项目，与 Schrödinger 或 PyMOL 项目不存在官方隶属或背书关系。[接口分析](docs/INTERNALS.md) 记录正常 API 边界与行为测试。

样例数据的存档与原始论文署名见[样例说明](examples/README.md)。研究中使用坐标时，请引用原始结构论文。
