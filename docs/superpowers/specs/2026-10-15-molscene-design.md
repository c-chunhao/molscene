# MolScene：PyMOL 可复现制图与分析工具

日期：2026-10-15。用户已选择：独立工具调用现有 PyMOL，发布自有代码、测试与接口分析报告。

## 目标与验收

让研究者用一条命令把本地 PDB/mmCIF 转成结构图、可在 PyMOL 打开的会话、可重放命令及可追溯 JSON。项目名称暂定 MolScene，公开仓库暂定 `c-chunhao/molscene`，发布前核对名称可用性。项目正常介绍功能与证据，不突出 AI 生成；如实说明来源、依赖和验证范围。项目热度是长期目标，不能承诺 stars 或排名。

成功标准：在本机已安装的 PyMOL 3.1.6.1 / Python 3.10.18 中，固定测试全部通过；真实 RCSB 样例的概览、配体口袋和叠合图能正确解码且人工目检；输出包含 PNG、PSE、PML、JSON；命令行错误清晰且失败返回非零；README 的命令真实可运行；公开仓库与发布版可访问。

## 组件与数据流

采用 Python 标准库实现 CLI 与报告，运行时从用户自己的 Python 环境导入 `pymol2`，通过独立上下文操作正常 PyMOL API。不复制或修改商业模块、二进制、许可证或界面资产。自有代码使用 MIT；PyMOL 为独立依赖并保留来源与商标说明。

CLI 输入配置 → 校验输入与参数 → 启动 PyMOL 上下文 → 加载固定名称的结构对象 → 选择/显示/测量/叠合 → ray 渲染 → 保存 PSE/PML/JSON → 验证文件存在并报告输出位置。

核心模块按职责划分为 CLI、输入及配置校验、PyMOL 场景与分析、产物与追溯报告。所有 PyMOL 调用使用固定对象名，用户文件名不会作为脚本代码。PML 用正确的 Python 字符串字面量记录路径和选择，不拼接未经转义的命令。

## 首版接口

- `python -m molscene doctor`：检测当前解释器、PyMOL 版本及正常上下文启动。
- `python -m molscene render INPUT --output DIR [--style cartoon|surface|pocket] [--ligand SELECTION] [--radius 5] [--width 1600 --height 1200 --dpi 300]`：概览默认按链着色，配体 sticks；pocket 明确要求有效配体选择，以距离邻近定义口袋，并提示这不是结合能或化学相互作用判定。
- `python -m molscene measure INPUT --selection-a SELECTION --selection-b SELECTION --output DIR`：两端各必须恰好选中一个原子，以 Å 输出几何距离和测量场景。
- `python -m molscene align MOBILE TARGET --output DIR [--selection 'polymer.protein and name CA']`：记录 `cmd.align` 返回的 RMSD、配对数及参数，输出两结构叠合图。默认针对相似蛋白，不能宣称适用任意蛋白。
- `python -m molscene selftest --output DIR`：离线生成固定几何/肽链，验证基本 API 与输出；记录逐项结果，任何失败使程序返回非零。

不在首版加入 GUI、联网自动抓取、批量队列、轨迹电影、预测或插件管理。下载真实样例采用独立示例脚本；主工具的本地文件流程可离线运行。

## 产物与错误处理

每次成功运行输出 `scene.png`、`scene.pse`、`scene.pml`、`report.json`。报告包括工具版本、UTC 时间、PyMOL 版本、输入文件名及 SHA-256、参数、原子/残基/链数、分析数值和产物名称。不在公共样例报告记录用户绝对路径、许可证或环境变量。

拒绝不存在/不支持的输入、空选择、非单原子测量、不合理图片尺寸、非有限数值、无效口袋半径、相同输入覆盖及非空输出目录（可用明确 `--overwrite` 覆盖本工具的固定产物，不删除目录）。PyMOL 不可用时给出使用已有 PyMOL Python 或开放源码后端的安装说明，不自动下载商业软件。

## 测试与发布

标准库 unittest 测 CLI 和关键边界；真实 PyMOL 集成测试检查 PDB/mmCIF 加载、选择数量、表示标志、已知 5 Å 距离、刚体变换叠合后的接近零 RMSD、会话恢复及非单色 PNG 尺寸。PML 在新的 PyMOL 上下文重放并对比主要状态。PNG 使用 Pillow 进行测试检查，核心工具不额外依赖 Pillow。

真实公开样例：1UBQ（结构概览，660 原子、76 残基）与 1HSG（蛋白/配体口袋），下载来源、SHA-256 和引用写入样例说明。商业后端实测结果单独标注；开源后端仅在实际运行后才声称兼容。CI 若可安装开放源码后端，另记录其精确版本，避免把仅单元测试成功当成集成成功。

发布包含中英文 README、真实图、快速开始、接口研究报告、测试报告、MIT LICENSE、贡献说明、issue 模板与 CI。不上传安装目录、商业源码、许可证、私人结构或本机凭据。GitHub 发布包括有描述/topics 的公共仓库和首个版本 tag/release；发布后检查远程文件和工作流结果。额外对外宣传、邮件或社区发帖需另有明确指令。

## 接口分析边界

本机观察证据：安装包 metadata 标注 proprietary；版本 3.1.6.1；Python 包 `pymol`、`pymol2`、`pmg_qt`、`chempy` 等；native extension `_cmd.cp310-win_amd64.pyd`；公开 Python 命令层把显示、加载、叠合及导出转交 native core。报告可记录文件名、大小、调用链和行为测试，但不复制专有模块。不把正常 API 调用描述为完成商业版全部逆向或复刻。

参考：[官方源码](https://github.com/schrodinger/pymol-open-source)、[许可](https://github.com/schrodinger/pymol-open-source/blob/master/LICENSE)、[命令参考](https://pymol.org/pymol-command-ref.html)、[1UBQ](https://www.rcsb.org/structure/1UBQ)、[1HSG](https://www.rcsb.org/structure/1HSG)。
