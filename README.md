# AI 数据分析助手

一个基于 AI 大模型的 Web 应用，支持两种工作模式：

- **数据分析**：上传 CSV / Excel，用自然语言提问，AI 自动生成 pandas 分析代码并在沙箱中执行，返回结论、结果表格和 ECharts 交互图表
- **知识库问答（RAG）**：上传 TXT / MD / PDF 文档入库，基于文档内容问答，回答附带原文出处，不编造内容

核心特性：

- **多用户账号体系**：注册 / 登录（bcrypt 密码哈希 + session cookie 鉴权），数据按用户隔离；管理员可进入后台管理用户
- **会话历史**：对话自动保存为会话，侧边栏可切换 / 重命名 / 删除，多轮对话上下文完整保留
- **数据集挂载**：数据分析模式下把文件挂载为 `df1 / df2……`，挂载状态刷新后保留，且严格按当前账号隔离
- **知识库文件夹分类**：文档可归入不同知识库（文件夹），支持新建 / 重命名 / 删除 / 移动文件
- **文件级检索控制**：每个文档可单独勾选是否参与检索，按需组合知识库与文档
- **多轮对话上下文**：RAG 模式下自动改写追问为独立完整问题，提升检索准确率
- **异步解析与状态追踪**：文档上传后后台解析向量化，界面实时显示解析中 / 已就绪 / 失败状态

技术栈：

- 后端：FastAPI + LangChain（RAG）+ OpenAI SDK（对话/分析）+ Chroma（向量库）+ pandas + SQLAlchemy 2.0（SQLite）
- 前端：Vue 3 + Vite + TypeScript + Element Plus + Pinia + ECharts

## 文件说明

| 文件/目录 | 作用 |
|---|---|
| `main.py` | FastAPI 后端主程序。路由：`/auth` 注册登录、`/chat` 流式对话（SSE，支持 chat/analysis/rag 三种模式）、`/sessions` 会话历史、`/upload` 数据文件上传、`/kb/*` 知识库与文档、`/datasets` 数据集、`/admin` 管理员接口 |
| `llm.py` | 模型调用层。集中管理 API_KEY / BASE_URL / MODEL 配置、各场景 prompt 模板（普通聊天 / 数据分析 / RAG / 追问改写）、流式调用与断线自动重试。**换模型或换框架只改这个文件** |
| `kb.py` | RAG 知识库模块（LangChain 实现）。文件夹与文档元数据管理、文档解析（TXT/MD/PDF/CSV）→ 中文切分（500 字/段）→ text-embedding-v3 向量化 → Chroma 存储 → 按用户 / 知识库 / 文档过滤的相似度检索 top-2 |
| `auth.py` | 账号与鉴权模块。注册 / 登录 / 登出、bcrypt 密码校验、session cookie、用户操作审计日志 |
| `db.py` | SQLAlchemy 2.0 数据访问层。用户、会话、消息、分析结果、知识库元数据等表的声明式模型（连接串读 `.env` 的 `DATABASE_URL`，默认 SQLite） |
| `runner.py` | 沙箱执行器。在独立子进程中运行模型生成的 pandas 代码（30 秒超时、只读、禁联网），输出结构化结果（结论 / 表格 / ECharts 图表配置） |
| `frontend/` | **Vue 3 前端工程**。Vite + TypeScript + Element Plus + Pinia；`src/api` 接口封装、`src/stores` Pinia 状态（auth/sessions/kb）、`src/components` 聊天与知识库组件、`src/views` 登录 / 聊天 / 管理页 |
| `static/index.html` | 旧版原生 HTML/JS 页面（后端 8000 端口根路由仍提供，用于兼容；新版界面请用 Vite 5173） |
| `.env` | 本地私密配置：API_KEY、BASE_URL、MODEL、EMBEDDING_MODEL、DATABASE_URL 等。**不会被 git 提交** |
| `.env.example` | 配置模板。新环境部署时复制为 `.env` 并填入自己的配置 |
| `requirements.txt` | Python 依赖清单（fastapi、langchain、chromadb、pandas、sqlalchemy 等） |
| `start.vbs` / `start.bat` / `stop.bat` | 后端一键启动 / 停止脚本（vbs 无窗口，bat 带控制台） |
| `uploads/` | 运行时目录：用户上传的数据文件（`uploads/kb/` 为知识库文档） |
| `chroma_db/` | 运行时目录：RAG 向量库的本地持久化数据 |

## 快速开始

环境要求：Windows + Python 3.10+ + Node.js 18+

```powershell
# 1. 克隆并进入目录
git clone https://github.com/atui1023/AI_dateanalyse.git aidataanalysis
cd aidataanalysis

# 2. 后端：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置
copy .env.example .env
# 编辑 .env，填入阿里云百炼 API Key（https://bailian.console.aliyun.com/）和 DATABASE_URL（默认 SQLite，可留用模板默认值）

# 4. 启动后端（127.0.0.1:8000，提供 API）
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
# 或直接双击 start.vbs / start.bat

# 5. 启动前端（另开一个终端；127.0.0.1:5173，接口经 Vite 代理转发到 8000）
cd frontend
npm install
npm run dev
```

浏览器打开 **http://localhost:5173** 即为新版 Vue 界面（首次使用请先注册账号，或用 `.env` 中 `ADMIN_PASSWORD` 设置的默认管理员登录）。

> 说明：开发期前端走 Vite 5173、后端走 8000，二者同源代理，登录 cookie 正常；`http://127.0.0.1:8000/` 目前仍是旧版静态页。前端生产构建用 `cd frontend && npm run build`（产物在 `frontend/dist`）。

## 使用说明

1. **注册 / 登录**：首次进入先注册账号并登录；各账号的知识库、数据集、对话互相隔离
2. **数据分析**：上传 CSV/Excel（最多 10 个，可多选）→ 切换到「数据分析」模式并挂载文件 → 用中文提问，如"各区域销售额排名"、"对比两个文件的差异"；回答下方展示分析结论、结果表格和可视化图表
3. **知识库问答**：切到「知识库」模式 → 上传 TXT/MD/PDF（最多 10 个）→ 提问，如"住宿报销标准是多少"，回答下方可展开"依据来源"核对原文
4. **知识库管理**：点击右下角文件夹图标打开管理面板，可新建知识库、重命名、删除，以及在知识库之间移动文档
5. **检索范围控制**：在知识库管理面板中勾选或取消勾选整个知识库或单个文档，仅勾选的内容参与检索
6. **历史会话**：左侧边栏自动保存历史会话，可切换查看、重命名或删除
7. 三种模式均支持多轮对话（追问上下文）与流式输出

## 注意事项

- 模型调用按 token 计费，走你自己的 API Key；前端不接触 Key，全部由后端代理
- 扫描版（图片型）PDF 无法提取文字，入库时会提示
- 数据分析代码在受限沙箱中执行：只读、30 秒超时、禁止联网和文件读写
- `.env` 中的 API Key、数据库密码等请勿分享或提交到 git
