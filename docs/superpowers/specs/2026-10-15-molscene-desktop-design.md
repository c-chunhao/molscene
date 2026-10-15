# MolScene v0.2 桌面工作台与结构分析设计

日期：2026-10-15。状态：用户已批准，并授权后续计划、实现、测试与发布全权推进，无需逐项确认。基线：v0.1.0 / `10abab1`。

## 1. 目标与成功标准

用户要求在现有 MolScene 上继续加入图形界面、更多制图和分析功能，逐步接近原 PyMOL 的使用丰富度；真实测试通过后更新 `c-chunhao/molscene`，本次所有工程、环境、缓存、测试和发布产物仍放在 F 盘。

本轮目标为 v0.2.0：以开源 PyMOL 的原生桌面界面为三维交互基础，加入 MolScene 工作台，把常用的显示、科学制图、几何分析、表面积和结果导出变成面板操作。原生 PyMOL 提供的功能与 MolScene 新增功能在文档中分别说明。发布时记录实际覆盖范围与剩余差距，不宣称与商业版全面等价。

成功标准是：可启动真实桌面窗口，使用鼠标操作真实分子；面板计算与 CLI 对同一输入、原子选择和 state 给出一致结果；导出当前视角的结构图、可编辑会话及可复现记录；数值、交互、输出保护和跨平台回归通过，随后更新 GitHub 和 Release。

## 2. 路线比较与选择

| 路线 | 优点 | 代价 | 选择 |
| --- | --- | --- | --- |
| 原生 PyMOL Qt 窗口 + MolScene dock | 保留成熟的旋转、平移、缩放、拾取、对象与命令界面，集中建设新增工作流 | 需要验证 PyMOL/Qt 组合及启动时序 | 推荐采用 |
| 自建 Qt 主窗并嵌入 PyMOL viewport | 更自由的整体布局 | 需重建对象、菜单、输入映射与窗口生命周期管理 | 留作后续定制方向 |
| 浏览器前端 + 分析服务 | 浏览器访问方便 | 需要同步两套选择、相机、对象、会话和渲染语义 | 本轮不采用 |

本地开源 PyMOL 3.2.0a0 已包含 `pmg_qt` 和关键 GUI 资源，尚未安装 Qt binding。候选 GUI 依赖为 `PySide6-Essentials==6.8.1` 及其同版 shiboken6；实际兼容性须经运行验证。CLI 核心继续不强制依赖 Qt。第三方组件保留各自许可与归属，发布包以 MolScene 自有代码为主。

## 3. 桌面界面与操作

新增 `molscene gui` 启动入口。中心保留原生三维视口，右侧为可关闭、再次打开的 MolScene dock，分为“结构与显示”“制图与会话”“分析”“结果”四页。

- 加载多个本地 PDB/mmCIF，列出分子对象、链、状态数和命名选择；支持刷新、显示/隐藏、定位、重命名和删除对象。删除和改名只作用于明确选中的对象。
- 打开 PSE 走独立的会话恢复路径，保留原有对象名、显示、视角和状态，不套用新结构默认样式；保留上游会话安全处理。
- 支持输入 PyMOL selection，以及将当前选择或原生拾取的原子用于测量。面板给出实际匹配的对象和原子数，空选择或需要单原子的操作匹配多原子时明确失败。
- 操作中显示状态和进度；错误在界面内可读，失败后可继续使用。重复打开菜单仅恢复同一个 dock。
- 继承原生窗口已有的命令界面和交互功能，不复制商业安装中的程序、界面资源或扩展模块。

## 4. 制图和场景功能

显示方式支持 cartoon、ribbon、sticks、lines、spheres、surface、mesh、dots、nonbonded，可作用于当前选择或指定对象。保留原有 overview、surface、pocket 预设，并增加统一的显示/隐藏操作。

着色支持按链、按元素、单色、序列渐变、B-factor。B-factor 色标须记录实际数值范围；分析不能覆盖用户原始 B-factor。原有 CA-only cartoon 设置只对真正的 CA-only 对象启用。

图像设置包括宽、高、DPI、白/黑/透明背景、光线追踪、表面透明度、stick 半径和标签。GUI 导出保留当前相机，只有用户点击“定位/重置视角”时才重新 orient。支持命名场景书签的保存、恢复和删除，书签随 PSE 保存。

CLI 的 `render` 同步开放 representation、selection 与 coloring 选项。原有 `--style cartoon/surface/pocket` 保留 v0.1 的蛋白中心语义；新增显式 `--representation` 路径对指定选择应用表示方式，不能同时与 pocket 预设使用。cartoon/ribbon 要求适用的聚合物；sticks/lines/spheres 等可用于非蛋白分子，空或不可显示的选择明确失败。v0.1 原有命令、选项默认值和报告字段保持兼容。新增参数明确区分图像样式与物理计算。

## 5. 科学分析范围

### 5.1 显式状态

所有新增分析和相关渲染/测量 API 都显式指定正整数 state。CLI 默认 state 1；GUI 在点击运行时固定当时选中的 state。选择表达式（包括空间条件）只在指定 state 的坐标集合中解析，再验证实际入选对象；禁止用 backend 的 singleton fallback 隐式代替缺失 state。混合对象的 all 表示本态实际有坐标的原子，直接选择本态不存在的对象为空并失败，不复用该对象其它帧坐标。结果记录此口径和本态实际原子/对象数。叠合分别提供 mobile_state 与 target_state。

多状态文件保存在会话中，但本轮数值结果针对一次明确的 state；不把多个状态拼成一个原子集合，不把本轮统计称为轨迹分析。

### 5.2 几何测量

保留距离，增加角度 A–B–C 和有符号二面角 A–B–C–D；分别要求 2、3、4 个独立的单原子选择。距离单位 Å，角度单位度，角度 B 为顶点；二面角采用 PyMOL 公开 API 的有符号约定。重复点、零长度向量及导致二面角未定义的共线情形明确拒绝。报告保存原子身份和 state，图中显示对应测量标注。

### 5.3 几何接触表

输入两个 selection、显式 state 与有限正 cutoff，默认 4 Å、仅重原子。输出原子中心距离不大于 cutoff 的无向原子对；排除自身配对，对重叠选择的 A–B/B–A 去重，跨对象的相同 index 保持独立。

每端身份包含 object、index、segi、chain、resi、resn、atom name、element、alt 和 state；按距离和完整身份稳定排序。另按残基对汇总最小距离与原子对数。结果为几何接近，不自动解释成氢键或亲和力。

最多允许 100000 个唯一原子对，超过时明确失败而不输出截断结果；使用分块或空间检索避免先分配完整笛卡尔积。图中至多画最短 200 条连线，并记录显示数与总数；CSV 保留全部成功计算的结果。无接触是有效的零行结果。

### 5.4 溶剂可及表面积 SASA

输入 target selection 与 context selection。默认 context 为选定分子对象内的非溶剂重原子，target 必须是过滤后 context 的子集；GUI 可明确选择多个对象共同作为 context。默认 altloc 策略为 blank+A，允许用户显式选择 all，并报告保留/过滤数量。

计算使用 dot_solvent=1、solvent_radius=1.4 Å、dot_density=3，输出 Å²、参数、过滤策略、总量和每残基贡献。将完整 context 合并为临时单对象，保留源原子映射，一次计算原子面积再汇总；这样保留跨残基和跨对象遮挡。只在临时数据上执行 load_b，用户原始 B-factor、坐标、对象和设置保持完整。target 子集的面积仍在完整 context 中求和，不能逐残基孤立计算后相加。

### 5.5 结构概况、坐标序列和叠合

提供对象/链/state 统计、原子类别、元素组成、蛋白/核酸残基数及坐标序列导出。链键包含 object、segi、chain；残基编号保留插入码和空链。新字段区分残基位点与不同 residue name，旧统计字段含义不变。

坐标序列来自实际存在的 guide atoms，未知字符标准化为 X，并保存残基到序列位置映射；不推断缺失残基或声称得到完整 SEQRES。输出 FASTA 与链摘要 CSV。

保留现有结构叠合，并增加 align/super 算法选择、cutoff/cycles 及两个对象的 state 控件。保存完整 backend 返回值及明确标注的 RMSD/匹配原子数；叠合变换在 GUI 中可见，不能仅在后台算出一个数。

## 6. 批量工作流

增加本地目录的批量制图入口，可选择 preset、着色和图像参数。按稳定顺序逐文件处理，每个任务有独立输出子目录；使用序号加安全名称避免同名输入冲突。批处理输出根与输入目录不得互为祖先。

批量任务记录逐项成功/失败与汇总，任何项失败时 CLI 非零退出；取消后保留已经完成的结果，不把未执行项目标为成功。GUI 后台运行此类任务，进度、失败及取消状态可见。

## 7. 架构与执行隔离

- 科学操作层接受实例绑定的 cmd，不导入 Qt；GUI 与 CLI 共用同一数值定义和参数校验，避免两套算法。
- CLI 继续以隔离 pymol2.PyMOL context 执行文件工作流；Qt 导入仅发生在 gui 入口，缺 Qt 时给出明确安装说明，原有 CLI help/doctor 不受影响。
- GUI controller 绑定实际主窗口 cmd；Qt 控件和 live 场景操作在 Qt 主线程串行执行。通过官方插件初始化/延迟入口安装 dock，不等待阻塞的 launch 返回，也不创建第二个 QApplication。
- 长耗时的 SASA、ray 导出和批处理使用明确的场景快照与独立 worker 进程。worker 不同时修改 live GUI；使用参数数组调用子进程，不将选择或文件名拼接成 shell/PyMOL 命令。
- 每份分析记录输入快照标识、选择、state 和计算时间。导出前检查其与当前场景是否一致；旧快照结果明确标注，不冒充当前场景结果。
- 模块按职责拆分：科学计算、样式、输出保护、桌面控制/面板、worker。针对现有 engine 增长进行必要拆分，保留公开 Python 函数与 CLI 兼容。

## 8. 输出、复现与保护

现有四种产物与 inputs/ 目录继续支持。新分析按类型增加 contacts.csv、sasa.csv、chains.csv、sequence.fasta；文件名由固定清单管理。新增整数 schema_version=2，旧字段及类型保持兼容，数值须 finite，JSON 使用 allow_nan=False，产物路径为输出目录内相对路径。

GUI 自由交互通过 PSE 现场快照保存。其 PML 可相对加载随包提供的 PSE 并恢复记录的视角/state；文档明确这一回放依赖随附 PSE。由 MolScene 坐标工作流构建的 CLI PML 仍可按记录的 API 操作重放，不能声称把任意原生交互全部展开成纯坐标脚本。

保留非空目录显式覆盖、输入祖先冲突、链接/重解析点、Windows 8.3 别名、固定产物类型检查；完成 stage 后复查再提交，只替换工具所有的产物，失败不破坏原输入或旧的有效输出。多个文件替换不描述为抵御系统崩溃的整目录事务。

## 9. F 盘与发布约束

公开文档以 `F:/path/to/molscene` 表示主工程占位路径（请替换为实际项目目录）。本次开发隔离目录如需新增，也必须位于同一 F 盘项目父目录；环境、包缓存、临时目录、截图、测试日志和 dist 全部明确指向 F 盘。保留迁移备份，不触碰原先独立安装的 PyMOL。

产品代码不硬编码本机个人路径。任务中的 GUI 工作空间/输出目录配置为 F 盘；对普通安装用户仍允许选择自己的工作目录。发布仅包含自有代码、必要文档和可公开示例，不提交本机运行环境、审计草稿、商业文件或许可材料。

测试通过后更新同一 GitHub 仓库，发布 v0.2.0 安装包、源码、真实示例与校验文件；保留 v0.1.0。更新中英文说明、界面截图、接口分析、测试报告和功能覆盖表，准确区分已有功能、新增功能与尚未覆盖的商业功能。

## 10. 验证与验收

1. 保留并运行现有 36 项回归和 9 项自测，必要时扩展自测；无 PyMOL/无 Qt 的普通 Python 仍可解析 CLI help，并清楚报告缺失依赖。
2. 合成几何独立验证 3–4–5 距离、90°/180°角和正负二面角，覆盖退化点与非法选择；误差不超过 1e-4 Å/度。
3. contacts 用独立欧氏距离穷举作小数据 oracle，覆盖恰在 cutoff、重叠选择、跨对象相同 index、零结果、重复去重与上限失败。
4. SASA 用单球 4π(r+probe)²（相对误差不超过 1%）、远离/相交双球、跨对象遮挡及残基和等于总量验证，并断言计算前后原 B-factor/坐标/设置不变。
5. 双状态合成结构验证 state 1/2 数值和场景不同，报告、会话、回放一致；不存在的 state 失败且不提交产物。
6. 使用现有固定哈希的 1UBQ PDB/mmCIF 和 1HSG fixture 验证真实结构、序列、contacts、SASA、叠合与口袋统计；不靠未入库的 .audit 样例跳过测试。
7. Qt 控件事件测试覆盖一次性 dock、菜单重开、选择/对象变更、参数、结果表、错误恢复、后台进度和取消。使用真实 viewport 验证旋转/缩放后 get_view 改变、拾取/选择、窗口生命周期和实际结构显示；只有 offscreen 控件通过不算 OpenGL 通过。
8. 检查当前视角 PNG 的尺寸、透明背景、非空结构和实际视觉质量；PSE 重载和新 context PML 回放比较状态、原子、颜色、表示与视角。保留中文、空格、引号、分号、$()、搬迁和删除原输入后的回归。
9. GitHub CI 保留 Windows/Linux Python 测试矩阵并加入可用的真实 backend/GUI 集成任务。分别报告未装依赖导致的合理跳过、实际执行和失败，不把缺 OpenGL 的跳过算作桌面验收通过。
10. 本机实际 GUI 与至少一条 CI 真实 backend 集成通过、独立代码审查完成、发布文件哈希核对后更新 Release。

## 11. 本轮边界与后续方向

本轮覆盖原生三维交互、常用表示/着色、基础场景管理及上述分析。轨迹统计、独立电影时间轴、电子密度专用工作流、静电势、自动生物学组装、经过验证的氢键化学判定和商业独有组件作为后续独立里程碑；文档列明差距。

## 12. 调研依据

- [PyMOL 启动](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/__init__.py)、[官方 Qt GUI](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pmg_qt/pymol_qt_gui.py)、[Qt binding 包装](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/Qt/__init__.py)。
- [官方命令参考](https://pymol.org/pymol-command-ref.html)、[querying.py](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/querying.py)、[Executive.cpp 面积实现](https://github.com/schrodinger/pymol-open-source/blob/master/layer3/Executive.cpp)、[exporting.py](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/exporting.py)。
- [Qt Essentials 模块说明](https://doc.qt.io/qtforpython-6.8/package_details.html)、[候选 PySide6-Essentials 6.8.1](https://pypi.org/project/PySide6-Essentials/6.8.1/)。
- 本地只读实证：`.audit/desktop-discovery/technical-report.zh-CN.md` 与 `environment-evidence.json`；未将源码可行性当作实际 GUI 运行通过。
