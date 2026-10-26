export type RecordData = Record<string, any>;
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data as T;
}
export const pretty = (data: unknown) => JSON.stringify(data, null, 2);
export const rate = (value: number | null | undefined) =>
  value == null ? "Not assessed" : (100 * value).toFixed(1) + "%";
export const seconds = (value: number | null | undefined) =>
  value == null ? "Not measured" : value.toFixed(3) + " s";
