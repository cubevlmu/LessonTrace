import { z } from "zod";
import { request } from "./client";
export const getPassage = (id: string) =>
  request(
    "passages/" + encodeURIComponent(id),
    z
      .object({
        passage_id: z.string(),
        content: z.string(),
        document_id: z.string(),
      })
      .passthrough(),
  );
