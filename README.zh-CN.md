# SSE Shrink

**中文简介：** 使用差异调试缩减仍能复现故障的 Server-Sent Events 流，并生成 Python 复现材料。

**English overview:** Use delta debugging to minimize a failure-inducing Server-Sent Events stream and export Python reproduction material.

## 使用 / Usage

Python 3.11+；需要用户提供判定函数以确认缩减后仍出现同一故障。

Python 3.11+; provide a predicate that confirms the same failure still occurs after minimization.

```text
python -m pip install -e .
sse-shrink --help
```

## 许可 / License

见 [LICENSE](LICENSE)。 / See [LICENSE](LICENSE).
