# NovelAgent

可移植的长篇小说创作 Agent。同一套代码在本机和云服务器上运行：正文、角色优化和参考审稿都走 **OpenAI 兼容 HTTP 接口**。默认预设是 [DeepSeek](https://api.deepseek.com)。量化模型如果要用，必须另起一个 HTTP 服务，本仓库不包含模型权重，进程内也不会加载权重。

网页在宽屏上是「左侧设定 + 右侧阅读」，窄屏（约 840px 以下，含手机）只留正文和底部输入，设定从「设定」按钮整页打开。

## 声明

本项目为 **雨里百跳** 原创。开发期间使用了 AI 辅助。允许任何人参考或在此基础上优化。许可协议为 [MIT](LICENSE)。

本仓库只发布 Agent 框架源码。不包含模型文件、训练数据集、会话记录和 API Key。NovelRef 抓到的参考段落只留在当轮内存里，不会写成数据集。

## 能做什么

- 多会话隔离。总纲、角色卡、记忆、参数都按会话保存，数据在 `.novelagent/`。
- 角色卡可多张，并可放进本机角色库再导入。
- 续写前组装提示：总纲、摘要、角色、召回的事件、风格参考、最近几轮对话。
- 全自动一轮：规划参考词 → 双站抓取并审稿 → 生成 → 清洗元文本 → 提取记忆。参考失败不会挡住正文。
- 流式输出、暂停（保留已写出的半段）、重新生成上一轮。
- 插件按文件夹加载，目录里放 `manifest.json` 即可。内置三个：叙事弧、世界书、小说笔法。
- 页面里可以填写并保存 API Key，不必改代码。密钥只显示末四位。

## 环境

- Python 3.10 及以上（开发时用过 3.12）
- 一个 OpenAI 兼容的 Chat Completions 接口，以及对应的 API Key
- 可选：本机代理。部分参考站直连不稳定，`NOVELREF_PROXY=auto` 会先直连，再尝试环境变量和常见本地代理端口

## 快速开始

```bash
cd novel-agent
python -m venv .venv
```

Windows：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

macOS / Linux：

```bash
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

编辑 `.env`，至少填上 `LLM_PRIMARY_API_KEY`。然后：

```bash
novel-agent
```

浏览器打开 <http://127.0.0.1:8787>。也可以不写 `.env`，启动后在页面的 **设定 → 参数** 里保存接口地址、模型和 API Key。页面保存的内容写在 `.novelagent/llm.json`，优先级高于 `.env` 里的这三项。

默认端口是 `8787`。被占用时在 `.env` 里改 `PORT`。

### 页面怎么用

1. 右上角 **新建** 一篇小说，或在设定里打开已有会话。
2. **会话** 里写总纲并保存。需要风格参考时选择关键词，场景词用逗号分隔。
3. **角色** 里保存角色卡。优化会再调一次辅助模型，把人设收成可注入的卡片。
4. 底部输入这一段要发生什么，点 **全自动续写**。手机上这个按钮显示为 **续写**。
5. 生成中可以暂停。暂停后的半段可以重新生成。

电脑上设定栏一直在左侧。手机上点左上角 **设定**，看完点 **完成**，或直接点一篇会话回到正文。

## 配置

`.env.example` 是云端画像：只调 API，不探测本地模型。

| 变量 | 作用 |
|---|---|
| `DEPLOY_PROFILE` | `cloud` 或 `local`。`cloud` 会强制关掉本地模型 |
| `MODEL_MODE` | `api_only` / `api_first` / `local_first` / `local_only`。云端画像下后两种会被改成 `api_only` |
| `LLM_PRIMARY_*` | 正文使用的接口、密钥、模型名 |
| `LLM_AUX_*` | 规划、审稿、角色优化、记忆提取。留空则跟主模型相同 |
| `NOVELREF_ENABLED` | 是否抓取风格参考 |
| `NOVELREF_LLM_JUDGE` | 是否用辅助模型给参考段打分 |
| `NOVELREF_PROXY` | `auto`、`direct`，或显式代理地址 |
| `NOVELAGENT_HOME` | 数据目录，默认 `./.novelagent` |
| `HOST` / `PORT` | 监听地址和端口，默认 `0.0.0.0:8787` |

本地量化服务是可选项，且不在本仓库里。它需要自己提供 `/v1/chat/completions`。把 `DEPLOY_PROFILE=local`、`LOCAL_LLM_ENABLED=true`，并把主模型的 `LLM_PRIMARY_BASE_URL` 指到那个服务。Agent 只发 HTTP 请求。

## Docker

镜像是云端画像，不含 CUDA，也不拷贝权重。

```bash
export LLM_PRIMARY_API_KEY=sk-...
docker compose up --build
```

Windows PowerShell 用 `$env:LLM_PRIMARY_API_KEY="sk-..."`。数据卷挂在容器内 `/data`。

## 目录

```
novel-agent/
├── src/novel_agent/     # 会话、记忆、提示词、路由、FastAPI、网页
├── src/novel_ref/       # 双站参考抓取与审稿
├── plugins/             # 内置插件（manifest.json + prompt.txt）
├── config/              # 默认生成参数、关键词、站点选择器
├── docs/                # 设计说明。以本 README 的使用方式为准
├── tests/               # 不联网的单元测试
├── Dockerfile
└── docker-compose.yml
```

设计备忘在 `docs/可移植总纲.md` 和 `docs/全自动工作流.md`。其中的阶段清单有一部分已经落后于代码：流式输出、暂停和重新生成已经做了，会话导入导出还没有。

## 用到的开源项目

这些是直接依赖，通过 pip 安装，没有把它们的源码拷进仓库。

| 项目 | 在这里的用途 | 许可证 |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | HTTP API | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | 运行服务 | BSD-3-Clause |
| [Starlette](https://github.com/encode/starlette) | FastAPI 底下的 ASGI 工具 | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) / pydantic-settings | 数据模型和 `.env` | MIT |
| [HTTPX](https://github.com/encode/httpx) | 调用模型接口 | BSD-3-Clause |
| [Requests](https://github.com/psf/requests) | 抓取参考页 | Apache-2.0 |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | 解析网页 | MIT |
| [lxml](https://lxml.de/) | HTML 解析器 | BSD-3-Clause |
| [PyYAML](https://github.com/yaml/pyyaml) | 读取配置 | MIT |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 启动时加载 `.env` | BSD-3-Clause |

开发依赖还有 pytest、pytest-asyncio、responses。

下面这些是交互或提示词上的参考，**不是代码分叉**，仓库里没有它们的源码：

- [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat) 这一套请求格式。DeepSeek 和多数自建服务都兼容它。
- [SillyTavern](https://github.com/SillyTavern/SillyTavern) 的角色卡、世界书（Lorebook）、故事阶段，以及 `manifest.json` 插件目录。
- SillyTavern 生态里的 NovelMode：正文优先、少写说明腔。对应内置插件「小说笔法」。
- 流式正文、暂停保留半段、生成时看到速度，交互上参考过 Dsh 一类写作前端。

## 已知问题

这是 0.1.0，能完成一轮续写，但下面这些还没收干净。

- **网页没有登录。** 默认监听 `0.0.0.0`。同一局域网的人可以打开页面并使用你的 Key。放到公网前需要自己加认证或反向代理，不要直接暴露端口。
- **密钥明文落在** `.novelagent/llm.json`。这个目录已在 `.gitignore` 里，不要把它提交上去。
- **设定对象被缓存。** 改 `.env` 后要重启进程。页面里保存 API Key 会立刻换上下一轮请求用的客户端，这一点不需要重启。
- **参考站结构一变，选择器就会失效。** 站点写在 `config/novel_ref/runtime.yaml`。`site_a` 现在是 alicesw.com，`site_b` 是 m.jinshuge.org。代码注释里还留着已经不可用的旧站名。代理不稳定时，某一站会空结果，另一站仍可能有段落。
- **参考抓取偏慢，而且受目标站限制。** 一轮会先取较长片段再截出更贴关键词的短段。目标站改版、验证码或拒绝访问时，这一轮就没有参考，正文仍会生成。
- **默认字数有两处。** 新会话和页面按单次最多约 6000 字。`config/app.default.yaml` 里仍写着 `max_tokens: 1600`。页面看到恰好是 1600 时会显示成 6000，旧会话文件本身不一定被改写。
- **重新生成不是流式的。** 暂停后再生成会等到整段结束才刷到页面上。
- **辅助模型如果没按 JSON 返回，** 角色优化、参考打分或记忆提取会跳过。正文生成不受这几步失败的影响。
- **没有会话导入导出。** 备份目前就是复制 `.novelagent/data/sessions/`。
- **测试不覆盖真实网页和真实站点。** `pytest` 只检查解析、记忆召回和会话存储。

## 可以接着做的方向

- 给服务加一个简单口令，再考虑放到公网。
- 会话整包导出、导入，方便换机器。
- 参考站改成可热插拔的适配器，并为每个站留一份离线 HTML 夹具，避免选择器改坏了才发现。
- 两个站并行抓，并给整段参考一个总超时。
- 重新生成也走流式接口。
- 页面里把主模型和辅助模型分成两套 Key，而不是辅助模型只在 `.env` 里配。
- 用接口返回的 usage 计算 token，而不是用字数估算速度。
- 插件目录变更后自动重载，不必重启。
- Docker 镜像改成非 root 用户运行。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

用户自己的插件放到 `.novelagent/plugins/<插件名>/`，里面至少有 `manifest.json`。启用状态记在 `.novelagent/plugins_state.json`。

## 许可

[MIT](LICENSE)。版权归 **雨里百跳**。欢迎参考和继续优化。使用第三方小说网站时，请自行遵守该网站的条款和 `robots.txt`。
