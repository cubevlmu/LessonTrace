import { z } from "zod";
import { videoSchema } from "../models/backend";
import { request, responseError } from "./client";
export const listVideos = () =>
  request("analysis/videos", z.array(videoSchema));
export const videoUrl = (id: string) =>
  "/api/context/analysis/videos/" + encodeURIComponent(id) + "/media";
export const deleteVideo = (id: string) =>
  request(
    "analysis/videos/" + encodeURIComponent(id),
    z.object({ status: z.string() }).passthrough(),
    undefined,
    "DELETE",
  );
const uploadSchema = z.object({
  video_id: z.string(),
  deduplicated: z.boolean(),
});
export function uploadVideo(
  file: File,
  onProgress: (percent: number) => void,
): Promise<z.infer<typeof uploadSchema>> {
  if (file.size > 512 * 1024 * 1024)
    return Promise.reject(Error("视频超过512 MiB限制"));
  if (!/\.(mp4|avi|mov|mkv)$/i.test(file.name))
    return Promise.reject(
      Error("仅支持MP4、AVI、MOV、MKV；服务端会继续校验实际编码"),
    );
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/context/analysis/videos");
    x.withCredentials = true;
    x.timeout = 1800000;
    x.upload.onprogress = (e) => {
      if (e.lengthComputable)
        onProgress(Math.round((e.loaded / e.total) * 100));
    };
    x.onerror = () =>
      reject(Error("网络中断，上传未完成，可重试；不使用本地模拟结果"));
    x.ontimeout = () =>
      reject(Error("上传或视频校验超时，可稍后查看已有视频并重试"));
    x.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(x.responseText);
      } catch {
        reject(Error("上传响应无法解析"));
        return;
      }
      if (x.status >= 200 && x.status < 300) {
        const p = uploadSchema.safeParse(body);
        if (p.success) resolve(p.data);
        else reject(Error("上传响应格式错误"));
      } else {
        if (x.status === 401)
          window.dispatchEvent(new Event("lessontrace:unauthorized"));
        reject(responseError(x.status, body));
      }
    };
    const f = new FormData();
    f.append("file", file);
    x.send(f);
  });
}
