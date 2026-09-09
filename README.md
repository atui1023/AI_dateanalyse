# AI 数据分析平台

一个面向业务数据分析和企业知识问答的 Web 平台。用户可以上传 CSV、Excel、TXT、Markdown 或 PDF，通过自然语言生成 pandas 分析代码、结果表格和 ECharts 图表，也可以使用 RAG 检索知识库文档。

## 主要能力

- **自然语言数据分析**：表格挂载为 `df1`、`df2` 等变量，由模型生成只读 pandas 代码并在独立子进程中执行。
- **分析结果可视化**：返回文字结论、结构化表格和 ECharts 柱状图、折线图或饼图。
- **知识库问答**：文档解析、分块、Embedding、Chroma 向量检索和来源引用。
- **多用户隔离**：bcrypt 密码哈希、Session Cookie、用户数据隔离和管理员后台。
- **会话持久化**：保存聊天消息和分析结果，重新进入会话后恢复表格与图表。
- **文件管理**：知识库文件夹、文档移动、启用/停用、失败重试及表格挂载。
- **Windows 兼容**：分析执行器统一使用 UTF-8，支持中文、`✓`、`✅` 等字符。

## 技术栈

- 后端：FastAPI、LangChain、OpenAI SDK、pandas、SQLAlchemy 2.0
- 数据库：SQLite（默认）或 MySQL
- 知识库：Chroma、OpenAI 兼容 Embedding API、pypdf
- 前端：Vue 3、Vite、TypeScript、Pinia、Element Plus、ECharts

## 目录结构

| 文件/目录 | 作用 |
|---|---|
| `main.py` | FastAPI 路由、SSE 聊天、文件上传、分析执行和会话持久化 |
| `llm.py` | 模型客户端、分析/RAG Prompt、流式输出和连接错误处理 |
| `runner.py` | 受限分析执行器，输出文字、表格和 ECharts 配置 |
| `kb.py` | 文档解析、切分、向量化、Chroma 检索和知识库管理 |
| `db.py` | 用户、会话、消息、分析结果和知识库 ORM 模型 |
| `auth.py` | 登录、注册、管理员接口、Session 和审计日志 |
| `frontend/` | Vue 3 前端工程 |
| `start.bat` | 推荐的 Windows 一键启动入口 |
| `start.vbs` | 无窗口启动入口 |
| `stop.bat` | 停止后端服务 |
| `ROADMAP.md` | 产品发展清单 |

## 环境要求

- Windows 10/11
- Python 3.10+
- Node.js 18+
- 一个支持 OpenAI 接口格式的聊天模型和 Embedding 服务

## 快速开始

```powershell
git clone https://github.com/atui1023/AI_dateanalyse.git
cd AI_dateanalyse

python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

cd frontend
npm install
cd ..

Copy-Item .env.example .env
```

编辑 `.env`：

```env
API_KEY=你的真实API密钥
BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL=qwen-plus
EMBEDDING_MODEL=text-embedding-v3

DATABASE_URL=sqlite:///./data_analysis.db
SESSION_SECRET=请替换为随机长字符串
ADMIN_PASSWORD=请设置管理员强密码
```

可以生成随机 Session 密钥：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 一键启动

双击 `start.bat`，脚本会：

1. 替换占用 `8000` 的旧后端进程。
2. 替换占用 `5173` 的旧前端进程。
3. 使用 UTF-8 环境启动 FastAPI 和 Vue。
4. 打开 `http://localhost:5173/`。

不希望显示命令窗口时可双击 `start.vbs`。默认管理员用户名为 `admin`，密码是 `.env` 中的 `ADMIN_PASSWORD`。

### 手动启动

```powershell
# 终端一：后端
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

# 终端二：前端
cd frontend
npm run dev -- --host 127.0.0.1 --strictPort
```

## 使用流程

### 数据分析

1. 登录后打开知识库文件面板。
2. 上传 CSV 或 Excel。
3. 点击文件右侧的加号，将文件挂载为 `df1`、`df2`。
4. 保持“数据分析”模式，用自然语言提出排名、趋势、占比、异常或关联分析问题。

表格挂载和 pandas 分析不依赖知识库向量化。即使 Embedding 暂时失败，CSV/Excel 仍可挂载分析。

### 知识库问答

1. 创建或选择知识库文件夹。
2. 上传 TXT、Markdown、PDF、CSV 或 Excel。
3. 等待状态变为“就绪”。
4. 切换到知识库模式并提问。

知识库必须正确配置 `API_KEY`、`BASE_URL` 和 `EMBEDDING_MODEL`。扫描版 PDF 暂不支持 OCR。

## 常见问题

### 模型服务连续连接失败

先检查 `.env` 中是否仍为占位值，并确认 `BASE_URL` 以 OpenAI 兼容 API 的版本路径结尾。

如果底层错误为 `WinError 10013`，说明 Windows 阻止了当前 Python 进程访问外网：

1. 运行 `stop.bat`。
2. 从资源管理器双击 `start.bat`，不要从限制网络的沙箱或自动化进程启动。
3. 必要时在 Windows 防火墙中允许项目虚拟环境和实际 Python 解释器访问网络。

### 文件显示“知识库处理失败”

这通常是 Embedding 服务不可用，不代表 CSV/Excel 无法读取。表格文件仍可点击加号挂载进行数据分析；RAG 检索则需要修复 Embedding 配置后点击“重试”。

### GBK 无法输出特殊字符

当前版本在后端和分析子进程中强制使用 UTF-8，并使用安全替换策略处理控制台编码。请通过最新启动脚本重新启动后再测试。

### 图表没有显示

- 确认模型生成了 `chart` 变量，并且 `series.data` 与横轴数据长度一致。
- 使用 `Ctrl + F5` 刷新前端。
- 历史分析结果会随会话消息重新加载；旧版本未保存的图表无法追溯恢复。

## 验证

```powershell
# Windows GBK/UTF-8 回归测试
.\.venv\Scripts\python.exe tests\test_runner_encoding.py

# 后端语法检查
.\.venv\Scripts\python.exe -m py_compile main.py llm.py kb.py db.py runner.py

# 前端类型检查和生产构建
cd frontend
npm run build
```

## 安全说明

- `.env`、数据库、上传文件、日志和向量库均已排除在 Git 之外。
- 不要把 API Key、数据库密码或 Session 密钥提交到仓库。
- 模型生成的代码仅用于只读分析，但生产部署仍应增加操作系统级 CPU、内存、文件和网络隔离。
- 模型和 Embedding 调用会使用你自己的 API 配额并产生费用。

后续功能规划见 [`ROADMAP.md`](ROADMAP.md)。
