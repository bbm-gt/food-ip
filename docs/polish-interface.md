# AI 润色接口（二期契约）

AI 润色是二期能力。当前实现的目的，是先固定剪辑引擎与外部 provider 之间的边界；内置 `null` provider 不读取或写入视频，恒定返回 `not_configured`。

## 数据契约

### `SegmentRef`

```python
class SegmentRef(BaseModel):
    project_id: str
    junction_id: int | None = None
    src_file: str = ""
    range_seconds: tuple[float, float] | None = None
```

- `project_id`：所属项目。
- `junction_id`：接缝润色时的零基接缝序号。
- `src_file`：已经由现有引擎渲染好的输入片段路径。当前占位请求为空字符串；真实 provider 接入前由编排层生成。
- `range_seconds`：可选的源文件时间范围 `(start, end)`。

### `PolishRequest`

```python
class PolishRequest(BaseModel):
    segment: SegmentRef
    goal: Literal[
        "harmonize_junction", "stabilize", "relight", "fix_audio"
    ] = "harmonize_junction"
    params: dict[str, Any] = Field(default_factory=dict)
```

目标依次表示接缝协调、画面稳定、重新布光和音频修复。`params` 是 provider 专属的扩展参数；provider 必须容忍空对象。

### `PolishResult`

```python
class PolishResult(BaseModel):
    segment_id: str
    output_file: str | None = None
    status: Literal["pending", "running", "done", "failed", "not_configured"]
    message: str | None = None
```

- `segment_id`：调用方可稳定关联的片段标识；当前接缝格式为 `<project_id>:junction:<index>`。
- `output_file`：成功时的替换片段路径；非 `done` 状态通常为 `None`。
- `status`：任务生命周期。当前唯一合法运行结果是 `not_configured`。
- `message`：适合向用户展示的中文状态或错误说明。

## Provider 协议与注册

Provider 实现异步协议：

```python
class PolishProvider(Protocol):
    async def polish(self, req: PolishRequest) -> PolishResult: ...
```

新 provider 放在 `backend/app/polish/providers/`，用注册装饰器声明名称，并确保模块被 `providers/__init__.py` 导入：

```python
from ..contract import PolishRequest, PolishResult
from ..registry import register

@register("example")
class ExampleProvider:
    async def polish(self, req: PolishRequest) -> PolishResult:
        output = await external_service(req.segment.src_file, req.params)
        return PolishResult(
            segment_id=f"{req.segment.project_id}:junction:{req.segment.junction_id}",
            output_file=str(output),
            status="done",
            message="润色完成",
        )
```

`REGISTRY: dict[str, PolishProvider]` 保存实例；`get(name)` 取 provider；`GET /api/projects/{id}/polish/providers` 返回已注册名称。前端应以 provider 列表和结果状态判断能力，不能把 HTTP `200` 等同于润色成功。

## 引擎边界

真实 provider 的接入必须遵守以下边界：

1. 剪辑引擎按权威 timeline 渲染一个确定的输入片段文件，并通过 `src_file` 交给 provider。
2. Provider 只处理该文件，输出新的替换片段文件路径；不得直接修改素材、项目 JSON、edits 或 timeline。
3. 编排层校验输出文件后决定是否替换后续导出输入。替换失败时保留原渲染片段。
4. Provider 可以异步执行并返回 `pending/running`，但状态持久化、重试、超时和取消由未来编排层负责。
5. 增加或更换 provider 不修改 `engine/` 的裁剪、转场和导出算法。

换言之：输入是“已渲染片段文件”，输出是“可替换文件路径”，provider 是可插拔边界，不是剪辑引擎的一部分。

## 当前 HTTP 行为

`POST /api/projects/{id}/polish/junctions/{j}` 会先检查项目以及相邻素材构成的接缝，再调用 `null` provider。合法请求返回 HTTP `200`：

```json
{
  "segment_id": "project123:junction:0",
  "output_file": null,
  "status": "not_configured",
  "message": "AI 润色功能尚未接入（二期实现）"
}
```

项目不存在返回 `404`；接缝越界返回 `400`；相邻素材不完整返回 `404`；请求 schema 不合法返回 `422`。当前不会发起网络请求、不会生成替换文件，也不会伪装成功。
