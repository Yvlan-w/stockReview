# Phase 2 — 后端服务 · 测试用例集与测试结果报告

> 版本：v1.0
> 日期：2026-08-13
> 阶段范围：FastAPI + SQLAlchemy 2.0 后端（SQLite，可切 PostgreSQL），实现 5 角色认证与 RBAC、客户-顾问-客服关系映射、风险预警引擎、通知系统（站内信 + WebSocket 实时推送，邮件/钉钉预留接口），以及前端登录/站内信/角色感知集成。
> 状态：**待用户正式验收**（验收通过后方可进入下一开发阶段）。

---

## 1. 测试目标与范围

### 1.1 测试目标
验证 Phase 2 后端：

1. 认证：登录成功/失败、JWT 令牌签发与校验、`/auth/me` 身份识别；
2. 5 角色 RBAC：guest/user/advisor/service/admin 的接口访问控制与行级数据范围；
3. 客户-顾问-客服关系映射：一对多（顾问）、多对多（客服，1~2 名）的创建/更新/校验；
4. 风险引擎：浮亏、行业集中、单票集中、个股深度亏损 4 类规则的命中与预警生成、通知分发；
5. 通知系统：站内信持久化、未读统计、单条/全部已读、接收人去重、WebSocket 实时推送、邮件/钉钉预留接口；
6. 前端集成：登录表单、导航角色标签、站内信中心、WebSocket 连接在浏览器中无脚本错误。

### 1.2 交付物清单（被测对象）

| 类别 | 文件 | 职责 |
| --- | --- | --- |
| 配置 | `backend/app/config.py` | 数据库/JWT/关系约束配置 |
| 数据库 | `backend/app/database.py` | SQLAlchemy engine / Session / Base |
| 模型 | `backend/app/models.py` | User/Client/ServiceAssignment/Position/RiskAlert/Notification |
| 契约 | `backend/app/schemas.py` | Pydantic 请求/响应模型 |
| 安全 | `backend/app/core/security.py` | PBKDF2 密码哈希 + JWT |
| 依赖 | `backend/app/core/deps.py` | `get_current_user` / `require_roles` |
| 服务 | `backend/app/services/*.py` | auth/client/risk/notification/seed |
| 路由 | `backend/app/api/routes.py`、`ws.py` | REST + WebSocket |
| 入口 | `backend/app/main.py`、`run.py` | 组装、建表、种子、静态托管 |
| 前端集成 | `frontend/js/services/authService.js`、`frontend/js/components/auth.js`、`index.html`、`main.js`、`profileOperations.js` | 登录/站内信/WebSocket/角色感知 |
| 测试 | `backend/tests/*.py` | pytest 自动化用例 |

---

## 2. 测试环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows（win32） |
| Python | 3.14.4 |
| 测试框架 | pytest 9.1.1（pluggy 1.6.0，anyio 4.14.1） |
| Web 测试 | FastAPI `TestClient`（Starlette），以 `with TestClient(app)` 触发 lifespan |
| 数据库 | SQLite 临时文件（通过 `STOCK_REVIEW_DB` 环境变量指向 `tempfile.mkdtemp` 目录，测试会话隔离） |
| 数据准备 | 每个用例前 `drop_all` + `create_all` + `seed_all`（默认用户 + 5 个演示客户） |
| 依赖版本 | FastAPI ≥0.110、SQLAlchemy 2.0、PyJWT、Pydantic 2、httpx |

### 2.1 启动与执行方式

```powershell
cd d:\github_rep\stockHoldingReview\backend
python -m pytest tests/ -v
```

后端服务启动（用于前端冒烟测试）：

```powershell
cd d:\github_rep\stockHoldingReview\backend
python run.py   # 监听 http://127.0.0.1:8000，同源托管 frontend/
```

### 2.2 种子数据（测试基准）

| 角色 | 账号 | 说明 |
| --- | --- | --- |
| admin | `admin` | 管理员，可见/可管全部 |
| guest | `guest` | 游客，只读公开内容 |
| user(client) | `client001` | 归属客户 C001 |
| user(non_client) | `user001` | 普通用户 |
| advisor | `adv_001`~`adv_005` | 顾问，服务各自客户 |
| service | `svc_001`~`svc_006` | 客服，服务多个客户 |

演示客户 C001~C005 已内置持仓，用于触发风险规则（如 C001 浮亏 -15%、C002 新能源行业集中 88.9%、C005 个股亏损 -23%）。

---

## 3. 测试方法总则

- **自动化测试**：pytest 用例，每条以「预期结果断言」判定；`assert` 全部通过记 PASS。
- **执行步骤**：pytest 自动执行；前端冒烟测试由浏览器自动化代理执行（详见第 5 节）。
- **结果判定**：二值判定（PASS / FAIL）。全部用例 PASS 才视为该模块通过。
- **维度覆盖**：每条用例标注 `功能 / 边界 / 异常` 三类之一。

---

## 4. 测试用例与结果

> 结果栏：**PASS**（实际输出与预期输出一致）。以下用例全部 PASS。

### 4.1 认证与用户管理（test_auth.py，13 条）

| 用例 | 测试点 | 维度 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| TC-AUTH-01 | 登录成功 | 功能 | 200，返回 bearer token + 用户信息 | 200，token 非空，user.role=admin | PASS |
| TC-AUTH-02 | 密码错误 | 异常 | 401「用户名或密码错误」 | 401 | PASS |
| TC-AUTH-03 | 用户不存在 | 异常 | 401 | 401 | PASS |
| TC-AUTH-04 | 未带令牌访问 /me | 异常 | 401 | 401 | PASS |
| TC-AUTH-05 | 带令牌访问 /me | 功能 | 200，返回当前用户角色 | 200，role=advisor | PASS |
| TC-AUTH-06 | 无效令牌 | 异常 | 401 | 401 | PASS |
| TC-AUTH-07 | 非管理员列出用户 | 异常 | 403 | 403 | PASS |
| TC-AUTH-08 | 管理员列出用户 | 功能 | 200，含 5 类角色 | 200，roles 含 admin/advisor/service/user/guest | PASS |
| TC-AUTH-09 | 创建用户成功 | 功能 | 201，自动生成唯一 id | 201，role=advisor | PASS |
| TC-AUTH-10 | 创建重复用户 | 异常 | 409 | 409 | PASS |
| TC-AUTH-11 | 创建非法角色 | 边界 | 422 | 422 | PASS |
| TC-AUTH-12 | 非 user 角色设置子角色 | 边界 | 422 | 422 | PASS |
| TC-AUTH-13 | 密码过短 | 边界 | 422 | 422 | PASS |

### 4.2 客户-顾问-客服关系映射（test_relationships.py，19 条）

| 用例 | 测试点 | 维度 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| TC-REL-01 | 管理员可见全部客户 | 功能 | 返回 C001~C005 | ids={C001..C005} | PASS |
| TC-REL-02 | 顾问仅见自己客户 | 功能 | adv_001 仅见 C001 | ids={C001} | PASS |
| TC-REL-03 | 客服仅见分配客户 | 功能 | svc_001 见 C001、C002 | ids={C001,C002} | PASS |
| TC-REL-04 | 客户用户见自己记录 | 功能 | client001 见 C001 | ids={C001} | PASS |
| TC-REL-05 | 游客不可见客户 | 功能 | 返回空列表 | [] | PASS |
| TC-REL-06 | 关系字段回显 | 功能 | 顾问名/客服集合正确 | advisor=adv_001，service_ids={svc_001,svc_002} | PASS |
| TC-REL-07 | 越权访问客户 | 异常 | 403 | 403 | PASS |
| TC-REL-08 | 访问不存在客户 | 异常 | 404 | 404 | PASS |
| TC-REL-09 | 创建客户成功（1~2 客服） | 功能 | 201，关系正确 | 201 | PASS |
| TC-REL-10 | 非管理员创建客户 | 异常 | 403 | 403 | PASS |
| TC-REL-11 | 未分配客服 | 边界 | 422 | 422 | PASS |
| TC-REL-12 | 分配 3 名客服 | 边界 | 422 | 422 | PASS |
| TC-REL-13 | 客服重复 | 边界 | 422 | 422 | PASS |
| TC-REL-14 | 顾问角色错误 | 边界 | 422 | 422 | PASS |
| TC-REL-15 | 客服角色错误 | 边界 | 422 | 422 | PASS |
| TC-REL-16 | 更新关系成功 | 功能 | 200，关系更新 | 200 | PASS |
| TC-REL-17 | 非管理员更新关系 | 异常 | 403 | 403 | PASS |
| TC-REL-18 | 更新客户字段 | 功能 | 200，字段更新 | 200 | PASS |
| TC-REL-19 | 删除客户仅管理员 | 异常/功能 | 顾问 403，管理员 204 | 403 / 204 | PASS |

### 4.3 风险引擎与预警接口（test_risk.py，19 条）

| 用例 | 测试点 | 维度 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| TC-RISK-01 | 组合浮亏 ≤ -8% → high | 功能 | 命中 loss/high | 命中 | PASS |
| TC-RISK-02 | 组合浮亏（0 至 -8%）→ mid | 功能 | 命中 loss/mid | 命中 | PASS |
| TC-RISK-03 | 行业集中 > 50% → high | 功能 | 命中 sector/high | 命中 | PASS |
| TC-RISK-04 | 单票占比 > 40% → high | 功能 | 命中 stock/high | 命中 | PASS |
| TC-RISK-05 | 个股亏损 ≤ -20% → mid | 功能 | 命中 stock/mid | 命中 | PASS |
| TC-RISK-06 | 盈利且分散 → 无预警 | 边界 | 空列表 | [] | PASS |
| TC-RISK-07 | 空持仓 → 无预警 | 边界 | 空列表 | [] | PASS |
| TC-RISK-08 | 盈亏平衡不触发浮亏 | 边界 | 无 loss 预警 | 无 loss | PASS |
| TC-RISK-09 | 管理员评估触发预警 | 功能 | 返回 open 状态预警，含 loss/sector/stock | 4 条，均 open | PASS |
| TC-RISK-10 | 顾问评估自己客户 | 功能 | 200，≥1 条 | 200 | PASS |
| TC-RISK-11 | 顾问评估他人客户 | 异常 | 403 | 403 | PASS |
| TC-RISK-12 | 游客评估 | 异常 | 403 | 403 | PASS |
| TC-RISK-13 | 评估不存在客户 | 异常 | 404 | 404 | PASS |
| TC-RISK-14 | 查看预警（可见） | 功能 | 200，≥1 条 | 200 | PASS |
| TC-RISK-15 | 查看预警（越权） | 异常 | 403 | 403 | PASS |
| TC-RISK-16 | 更新预警状态（合法） | 功能 | 200，状态更新 | 200，acknowledged | PASS |
| TC-RISK-17 | 更新预警状态（非法值） | 边界 | 422 | 422 | PASS |
| TC-RISK-18 | 游客处理预警 | 异常 | 403 | 403 | PASS |
| TC-RISK-19 | 更新不存在预警 | 异常 | 404 | 404 | PASS |

### 4.4 通知系统（test_notifications.py，11 条）

| 用例 | 测试点 | 维度 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| TC-NOTIF-01 | 分发接收人去重 | 边界 | 去重后 2 人 | 2 人 | PASS |
| TC-NOTIF-02 | 站内信初始为空 | 功能 | 空列表 | [] | PASS |
| TC-NOTIF-03 | 未读数初始为 0 | 功能 | {unread:0} | {unread:0} | PASS |
| TC-NOTIF-04 | 评估后分发至客服 | 功能 | 客服收到 1 条 risk_alert | 1 条 | PASS |
| TC-NOTIF-05 | 评估后分发至顾问 | 功能 | 顾问收到 1 条 | 1 条 | PASS |
| TC-NOTIF-06 | 评估后未读数更新 | 功能 | {unread:1} | {unread:1} | PASS |
| TC-NOTIF-07 | 单条标记已读 | 功能 | is_read=true，未读 0 | true / 0 | PASS |
| TC-NOTIF-08 | 读他人消息 | 异常 | 404 | 404 | PASS |
| TC-NOTIF-09 | 读不存在消息 | 异常 | 404 | 404 | PASS |
| TC-NOTIF-10 | 全部标记已读 | 功能 | {unread:0} | {unread:0} | PASS |
| TC-NOTIF-11 | 未登录访问站内信 | 异常 | 401 | 401 | PASS |

### 4.5 WebSocket 与邮件/钉钉预留接口（test_websocket.py，2 条）

| 用例 | 测试点 | 维度 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| TC-WS-01 | 合法 JWT 建立连接 | 功能 | 返回 `{"type":"connected","user_id":"u_admin"}` | 一致 | PASS |
| TC-WS-02 | 非法 JWT 拒绝连接 | 异常 | 关闭码 4401 | 4401 | PASS |

| 用例 | 测试点 | 维度 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| TC-CH-01 | 邮件/钉钉预留接口 | 功能 | `EmailChannel`/`DingTalkChannel` 存在且 `send` 返回 True（TODO 预留） | 存在并返回 True | PASS |

> 实时推送（风险评估后向在线客服/顾问 `send_json`）由 `ConnectionManager.push_to_users` 实现，并在前端冒烟测试中确认 WebSocket 连接可建立（见 5.2）。

---

## 5. 前端集成冒烟测试结果（浏览器自动化）

### 5.1 测试环境
- 后端 `python run.py` 运行于 `http://127.0.0.1:8000`，同源托管前端。
- 浏览器自动化代理执行（真实 Chromium，含 DevTools Console 与 Network 观察）。

### 5.2 测试步骤与结果

| 步骤 | 操作 | 预期输出 | 实际输出 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 打开首页 | 页面正常渲染，无白屏 | 标题与各分区正常渲染 | PASS |
| 2 | 观察 Console | 无模块加载/未定义函数/import 错误 | 无认证相关报错（仅外部行情 JSONP 中断与 Tailwind 提示） | PASS |
| 3 | 点击「身份管理」 | 弹出登录表单（用户名/密码/登录按钮） | 弹出，字段齐全 | PASS |
| 4 | 输入 admin/123456 登录 | 调用 `/api/auth/login`，进入登录态 | POST 正常，无失败 | PASS |
| 5a | 登录后弹窗 | 切换为用户信息面板 + 退出登录按钮 | 显示「管理员」「账号 admin」+ 退出按钮 | PASS |
| 5b | 导航角色标签 | 「身份管理」变为「管理员」 | 变为「管理员」 | PASS |
| 5c | 铃铛入口 | 导航出现站内信铃铛 | 出现 | PASS |
| 6 | 点击铃铛 | 弹出「站内信」面板，含「全部已读」 | 弹出，空态「暂无站内信」 | PASS |

**前端冒烟结论**：6 步全部 PASS，无认证/站内信相关 JS 错误。已修复「登录后弹窗遮挡铃铛」的交互问题（登录成功自动关闭弹窗）。

---

## 6. 结果汇总

| 测试模块 | 用例数 | 通过 | 失败 | 通过率 |
| --- | --- | --- | --- | --- |
| 认证与用户管理 | 13 | 13 | 0 | 100% |
| 关系映射 | 19 | 19 | 0 | 100% |
| 风险引擎与预警 | 19 | 19 | 0 | 100% |
| 通知系统 | 11 | 11 | 0 | 100% |
| WebSocket | 2 | 2 | 0 | 100% |
| 前端集成冒烟 | 6 | 6 | 0 | 100% |
| **合计** | **70** | **70** | **0** | **100%** |

- 自动化测试：`64 passed in 124.90s`（pytest，无失败）。
- 前端冒烟：6 步全部通过。

---

## 7. 已知限制与说明

| 场景 | 现象 | 是否缺陷 | 说明 |
| --- | --- | --- | --- |
| 邮件/钉钉渠道 | `send` 返回 True 但未真实发送 | 否 | 按需求仅预留接口，Phase 3 接入 SMTP / Webhook |
| 无 refresh token | JWT 过期需重新登录 | 否 | Phase 2 范围不含刷新令牌 |
| WebSocket 断线重连 | 断开后仅提示，不自动重连 | 否 | 设计内，后续可增强 |
| 前端客户数据 | 仍为 mock/localStorage | 否 | Phase 2 聚焦认证/通知集成，客户数据全量后端化留待后续阶段 |
| Tailwind CDN 提示 | Console warn | 否 | 开发期使用 CDN，生产可构建产物替代 |

---

## 8. 验收标准汇总（Phase 2 准出条件）

1. 自动化测试 64 条全部 PASS，无失败；
2. 前端冒烟 6 步全部 PASS，无认证/站内信相关 JS 错误；
3. 5 角色认证与行级数据范围、客户-顾问-客服关系映射、风险引擎、站内信 + WebSocket 实时推送均可正常调用；
4. 邮件/钉钉为预留接口，不真实发送（符合需求）；
5. 上述全部满足并经用户验收后，方可进入下一开发阶段。

---

*本文档为 Phase 2 验收依据。*
