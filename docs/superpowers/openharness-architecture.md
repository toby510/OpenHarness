# OpenHarness 系统架构图

## 整体架构（四层 + ohmo 应用包）

```mermaid
flowchart TB
    subgraph ohmo["ohmo 应用包（独立 PyPI 包）"]
        direction TB
        ohmo_gateway["ohmo/gateway/"]
        ohmo_ws["ohmo workspace (~/.ohmo/)"]

        subgraph gateway["聊天网关"]
            g_service["service.py<br/>生命周期管理"]
            g_bridge["bridge.py<br/>消息桥接"]
            g_router["router.py<br/>消息路由"]
            g_group["group_tool.py<br/>群组工具"]
            g_provider["provider_commands.py<br/>Provider 配置"]
        end

        subgraph ws["Workspace 文件"]
            ws_soul["soul.md"]
            ws_identity["identity.md"]
            ws_user["user.md"]
            ws_bootstrap["BOOTSTRAP.md"]
            ws_memory["memory/"]
            ws_gateway["gateway.json"]
        end
    end

    subgraph layer4["Layer 4: 应用层（消费 Agent Loop 的上层建筑）"]
        direction LR
        autopilot["autopilot/<br/>自主交付流水线"]
        coordinator["coordinator/<br/>多 Agent 编排"]
        swarm["swarm/<br/>Agent 生命周期"]
        tasks["tasks/<br/>后台任务管理"]
        channels["channels/<br/>外部 IM 接入"]
        bridge["bridge/<br/>子 CLI 会话桥接"]
        sandbox["sandbox/<br/>Docker 隔离执行"]
        personalization["personalization/<br/>自动提取用户偏好"]
    end

    subgraph layer3["Layer 3: UI & 交互层"]
        direction LR
        ui["ui/<br/>TUI 层"]
        commands["commands/<br/>斜杠命令注册表"]
        skills_ui["skills/<br/>技能系统（UI 暴露）"]
        voice["voice/<br/>语音输入"]
        keybindings["keybindings/<br/>键盘快捷键"]
        themes["themes/<br/>外观/样式"]
    end

    subgraph layer2["Layer 2: 元能力/扩展层"]
        direction LR
        plugins["plugins/<br/>第三方插件系统"]
        mcp["mcp/<br/>MCP 客户端"]
        skills["skills/<br/>按需知识加载"]
        prompts["prompts/<br/>运行时 prompt 构建"]
        memory["memory/<br/>持久化跨会话记忆"]
        services["services/<br/>后台服务"]
    end

    subgraph layer1["Layer 1: 核心引擎层（所有路径必经之地）"]
        direction TB
        engine["engine/<br/>Agent Loop 心脏"]
        tools["tools/<br/>工具基类 + 注册表"]
        permissions["permissions/<br/>安全决策引擎"]
        hooks["hooks/<br/>生命周期拦截"]
        api["api/<br/>LLM 抽象层"]
        auth["auth/<br/>统一认证"]
        config["config/<br/>配置解析"]
    end

    %% 依赖流向：上层依赖下层
    layer4 --> layer3
    layer3 --> layer2
    layer2 --> layer1

    %% ohmo 依赖核心 harness
    ohmo --> layer4
    ohmo --> layer3
    ohmo --> layer2
    ohmo --> layer1

    %% 核心引擎内部关系
    engine --> tools
    engine --> permissions
    engine --> hooks
    engine --> api
    api --> auth
    auth --> config
    tools --> permissions
    hooks --> tools

    %% 元能力层内部关系
    plugins --> skills
    mcp --> tools
    skills --> prompts
    prompts --> memory
    services --> engine

    %% 应用层内部关系
    autopilot --> swarm
    swarm --> tasks
    coordinator --> swarm
    channels --> bridge
    sandbox --> tools

    %% ohmo 网关内部关系
    g_service --> g_bridge
    g_bridge --> g_router
    g_router --> g_group
    g_router --> g_provider
    ohmo_gateway --> gateway
    ohmo_ws --> ws

    %% 样式定义
    classDef core fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    classDef meta fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    classDef ui fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    classDef app fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef ohmo fill:#fce4ec,stroke:#880e4f,stroke-width:2px
    classDef engine_core fill:#bbdefb,stroke:#0d47a1,stroke-width:3px

    class engine,tools,permissions,hooks,api,auth,config core
    class plugins,mcp,skills,prompts,memory,services meta
    class ui,commands,skills_ui,voice,keybindings,themes ui
    class autopilot,coordinator,swarm,tasks,channels,bridge,sandbox,personalization app
    class ohmo_gateway,ohmo_ws,g_service,g_bridge,g_router,g_group,g_provider,ws_soul,ws_identity,ws_user,ws_bootstrap,ws_memory,ws_gateway ohmo
    class engine engine_core
```

## 核心引擎数据流（工具执行流水线）

```mermaid
sequenceDiagram
    participant QE as QueryEngine
    participant LLM as LLM API
    participant Hook as HookExecutor
    participant Perm as PermissionChecker
    participant Tool as BaseTool
    participant Reg as ToolRegistry

    rect rgb(225, 245, 254)
        Note over QE,LLM: Agent Loop
        QE->>LLM: stream response
        LLM-->>QE: tool_use block
    end

    rect rgb(243, 229, 245)
        Note over QE,Hook: PreHook 阶段
        QE->>Hook: PreToolUse event
        Hook-->>QE: continue / block
    end

    rect rgb(255, 243, 224)
        Note over QE,Perm: 权限决策
        QE->>Perm: evaluate(tool, args, context)
        Perm-->>QE: allow / deny / confirm
    end

    alt allow
        rect rgb(232, 245, 233)
            Note over QE,Tool: 工具执行
            QE->>Reg: lookup(tool_name)
            Reg-->>QE: BaseTool instance
            QE->>Tool: execute(arguments, context)
            Tool-->>QE: ToolResult
        end

        rect rgb(243, 229, 245)
            Note over QE,Hook: PostHook 阶段
            QE->>Hook: PostToolUse event
            Hook-->>QE: continue
        end

        QE->>LLM: append tool_result
    else deny/confirm rejected
        QE->>LLM: append error/denied
    end
```

## 配置解析优先级

```mermaid
flowchart LR
    A["CLI arguments<br/>最高优先级"] --> B["环境变量"]
    B --> C["~/.openharness/settings.json"]
    C --> D["默认值<br/>最低优先级"]

    style A fill:#ffebee,stroke:#b71c1c
    style D fill:#e8f5e9,stroke:#1b5e20
```

## ohmo 网关架构

```mermaid
flowchart LR
    subgraph IM["外部 IM 平台"]
        feishu["飞书"]
        slack["Slack"]
        telegram["Telegram"]
        discord["Discord"]
    end

    subgraph gateway["ohmo/gateway/"]
        router["router.py<br/>消息路由"]
        bridge["bridge.py<br/>消息桥接"]
        service["service.py<br/>生命周期"]
        group["group_tool.py<br/>群组工具"]
        provider["provider_commands.py<br/>Provider 配置"]
    end

    subgraph harness["OpenHarness 核心"]
        engine["QueryEngine"]
        tools["ToolRegistry"]
        channels["channels/"]
    end

    feishu --> router
    slack --> router
    telegram --> router
    discord --> router

    router --> bridge
    bridge --> engine
    group --> bridge
    provider --> service
    service --> router

    engine --> tools
    channels --> router
```

## 技术栈概览

| 层级 | 核心技术 | 关键类/模块 |
|------|---------|-----------|
| Layer 1 | Anthropic SDK, OpenAI SDK, Pydantic | `QueryEngine`, `BaseTool`, `PermissionChecker`, `HookExecutor` |
| Layer 2 | JSON Schema, MCP Protocol, Markdown | `PluginManager`, `McpClient`, `SkillLoader` |
| Layer 3 | Textual, React, Ink | `TuiRuntime`, `CommandRegistry` |
| Layer 4 | Git, Docker, WebSocket | `AutopilotPipeline`, `TeamRegistry`, `ChannelBridge` |
| ohmo | FastAPI, aiohttp | `GatewayService`, `MessageRouter` |
