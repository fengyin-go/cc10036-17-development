# 多维分析查询与指标平台

面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编码、指标名称、业务域 |
| 多维立方 | `cube_builder` | OLAP立方 | 立方名称、事实表、维度列表 |
| 切片查询 | `slice_query` | 查询模板 | 模板名称、数据立方、查询维值 |
| 分群圈选 | `segment_rule` | 分群规则 | 分群名称、人群类型、筛选条件 |
| 看板布局 | `dashboard_layout` | 看板配置 | 看板名称、所属业务域、图表布局 |
| 即席导出 | `adhoc_export` | 导出任务 | 任务名称、查询SQL、导出格式 |
| 报表订阅 | `report_subscription` | 订阅配置 | 订阅名称、报表对象、推送周期 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则名称、监控指标、检测算法 |
| 下钻路径 | `drill_path` | 下钻配置 | 路径名称、起始维度、下钻层级 |
| 语义层 | `semantic_layer` | 语义定义 | 语义名称、物理表、字段映射 |
| 查询加速 | `query_accelerator` | 加速配置 | 配置名称、目标查询、加速方式 |
| 归因模型 | `attribution_model` | 归因配置 | 模型名称、归因对象、归因窗口 |
| 同期群分析 | `cohort_analysis` | 群组配置 | 群组名称、初始事件、回访事件 |
| 数据血缘 | `data_lineage` | 血缘节点 | 节点名称、节点类型、上游列表、事实表、来源系统 |
| 查询调度 | `query_scheduler` | 调度计划 | 计划名称、查询语句、执行周期、前驱节点 |
| 结果缓存 | `result_cache` | 缓存条目 | 缓存键、查询描述、结果行数 |
| 数据范围 | `olap_scope` | 范围规则 | 规则名称、数据范围、授权对象 |
| 实验分析 | `ab_analysis` | 实验配置 | 实验名称、对照组、实验组 |

## 跨系统血缘交接

血缘图需要在不同系统之间复算，`data_lineage` 支持按节点导出交接文件并在目标端载入：

1. 导出：`GET /api/lineage/exchange/export?node_ids=3,10,11`，文件里每个节点记下
   事实表出处（`fact_table` + `fact_origin`）和前驱节点键（`predecessors`）。
   留空 `node_ids` 导出全部在线节点；`GET /api/lineage/exchange/sample?kind=conflict`
   可生成三节点示例（一张订单日表与本地重合，另一张同名表来自外域）。
2. 载入：`POST /api/lineage/exchange/import`，先按事实表出处核对再映射节点：
   - 本地重合 / 外域已存在：复用已有节点；
   - 同名异源：按出处分别保留，不并入本地同名节点，并记入冲突清单；
   - 前驱键本地无法解析：记入 `missing_edges`，图版本状态为「待补齐」。
3. 同一文件（摘要相同）并发载入只生成一个图版本，后到请求返回 `duplicate: true`。
   `GET /api/lineage/exchange/versions` 列出所有版本，`{version_id}` 查看明细。
4. 调度详情 `GET /api/scheduler/{plan_id}/detail` 与
   `GET /api/scheduler/{plan_id}/partition-scope` 只用核过的前置关系推导分区刷新范围；
   仍缺边或出处待核时 `ready` 为 `false`，并在 `blockers` 中说明原因，
   `GET /api/scheduler/ready` 不会把这些计划当成就绪。
   计划 `血缘引用` 记录引用的图版本、引用类型（旧计划引用 / 新计划上游依赖）和由来。

前端入口：数据血缘页「跨系统交接」、查询调度页「调度详情」。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
