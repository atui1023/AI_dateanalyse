# AI 数据分析助手

一个基于 AI 大模型的 Web 应用，支持两种工作模式：

- **数据分析**：上传 CSV / Excel，用自然语言提问，AI 自动生成 pandas 分析代码并在沙箱中执行，返回结论、结果表格和 ECharts 交互图表
- **知识库问答（RAG）**：上传 TXT / MD / PDF 文档入库，基于文档内容问答，回答附带原文出处，不编造内容

核心特性：

- **知识库文件夹分类**：文档可归入不同知识库（文件夹），支持新建 / 重命名 / 删除 / 移动文件
- **文件级检索控制**：每个文档可单独勾选是否参与检索，按需组合知识库与文档
- **多轮对话上下文**：RAG 模式下自动改写追问为独立完整问题，提升检索准确率
- **异步解析与状态追踪**：文档上传后后台解析向量化，界面实时显示解析中 / 已就绪 / 失败状态

技术栈：FastAPI + LangChain（RAG）+ OpenAI SDK（对话/分析）+ Chroma（向量库）+ pandas + 原生 HTML/JS 前端

## 文件说明

| 文件/目录 | 作用 |
|---|---|
| `main.py` | FastAPI 后端主程序。路由：`/` 页面、`/upload` 数据文件上传、`/chat` 流式对话（SSE，支持 analysis/rag 两种模式）、`/kb/upload`、`/kb/documents`、`/kb/folders` 知识库接口、`/datasets/{id}` 删除数据集 |
| `llm.py` | 模型调用层。集中管理 API_KEY / BASE_URL / MODEL 配置、各场景 prompt 模板（普通聊天 / 数据分析 / RAG / 追问改写）、流式调用与断线自动重试。**换模型或换框架只改这个文件** |
| `kb.py` | RAG 知识库模块（LangChain 实现）。文件夹与文档元数据管理、文档解析（TXT/MD/PDF/CSV）→ 中文切分（500 字/段）→ text-embedding-v3 向量化 → Chroma 存储 → 按知识库 / 文档过滤的相似度检索 top-2 |
| `runner.py` | 沙箱执行器。在独立子进程中运行模型生成的 pandas 代码（30 秒超时、只读、禁联网），输出结构化结果（结论/表格/图表配置） |
| `static/index.html` | 前端单页面。聊天界面、模式切换（📊/📚）、文件上传与 📁 弹层管理、流式渲染、ECharts 图表、RAG 依据来源展示 |
| `.env` | 本地私密配置：API_KEY、BASE_URL、MODEL、EMBEDDING_MODEL。**不会被 git 提交** |
| `.env.example` | 配置模板。新环境部署时复制为 `.env` 并填入自己的 Key |
| `requirements.txt` | Python 依赖清单（fastapi、langchain、chromadb、pandas 等） |
| `.gitignore` | git 排除规则：`.env`、`.venv`、`uploads/`、`chroma_db/`、日志 |
| `start.vbs` | **推荐启动方式**（可建桌面快捷方式）：无窗口后台启动服务并自动打开浏览器 |
| `start.bat` | 备用启动方式：带控制台窗口，可看实时日志，关闭窗口即停止 |
| `stop.bat` | 停止后台运行的服务 |
| `uploads/` | 运行时目录：用户上传的数据文件（`uploads/kb/` 为知识库文档） |
| `chroma_db/` | 运行时目录：RAG 向量库的本地持久化数据 |
| `server.log` | 运行日志（start.vbs 方式启动时写入） |

## 快速开始

环境要求：Windows + Python 3.10+

```powershell
# 1. 克隆并进入目录
git clone https://github.com/atui1023/AI_dateanalyse.git aidataanalysis
cd aidataanalysis

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置
copy .env.example .env
# 编辑 .env，填入你自己的阿里云百炼 API Key（https://bailian.console.aliyun.com/）

# 4. 启动
.\start.bat
# 浏览器打开 http://127.0.0.1:8000/
```

## 使用说明

1. **数据分析**：点上传按钮选择 CSV/Excel（最多 10 个，可多选）→ 用中文提问，如"各区域销售额排名"、"对比两个文件的差异"
2. **知识库问答**：切到知识库模式 → 点上传按钮选择 TXT/MD/PDF（最多 10 个）→ 提问，如"住宿报销标准是多少"，回答下方可展开"依据来源"核对原文
3. **知识库管理**：点击文件夹图标打开管理面板，可新建知识库、重命名、删除，以及在知识库之间移动文档
4. **检索范围控制**：在知识库管理面板中，可勾选或取消勾选整个知识库或单个文档，仅勾选的内容参与检索
5. 两种模式均支持多轮对话（追问上下文）与流式输出

## 注意事项

- 模型调用按 token 计费，走你自己的 API Key；前端不接触 Key，全部由后端代理
- 扫描版（图片型）PDF 无法提取文字，入库时会提示
- 数据分析代码在受限沙箱中执行：只读、30 秒超时、禁止联网和文件读写
- `.env` 中的 API Key 请勿分享或提交到 git
