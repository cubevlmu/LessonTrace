import type { z } from "zod";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
const labels: Record<number, string> = {
  401: "登录已失效，请重新登录",
  403: "当前账户没有此操作权限",
  413: "视频超过512 MiB限制",
  422: "输入或视频内容不符合要求",
  503: "服务暂不可用",
};
export function responseError(status: number, body: unknown) {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? JSON.stringify(body.detail)
      : String(body || "");
  return new ApiError(
    status,
    `${labels[status] || "请求未完成"}${detail ? "：" + detail : ""}`,
  );
}
export async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  body?: unknown,
  method?: string,
): Promise<T> {
  const res = await fetch("/api/context/" + path, {
    method: method || (body ? "POST" : "GET"),
    credentials: "same-origin",
    headers:
      body instanceof FormData
        ? undefined
        : body
          ? { "Content-Type": "application/json" }
          : undefined,
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    if (res.status === 401)
      window.dispatchEvent(new Event("lessontrace:unauthorized"));
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      data = await res.text().catch(() => "");
    }
    throw responseError(res.status, data);
  }
  const parsed = schema.safeParse(await res.json());
  if (!parsed.success)
    throw new ApiError(
      0,
      "服务响应格式发生变化，请重试或联系维护者（未显示替代数据）",
    );
  return parsed.data;
}
