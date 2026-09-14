import type { VidXPFetch } from "./client";

interface CepNodeRuntime {
  require(name: "http" | "https"): NodeHttpModule;
}

interface NodeHttpModule {
  request(
    url: string,
    options: { headers: Record<string, string>; method: string },
    callback: (response: NodeResponse) => void,
  ): NodeRequest;
}

interface NodeResponse {
  headers: Record<string, string | string[] | undefined>;
  statusCode?: number;
  statusMessage?: string;
  on(event: "data", listener: (chunk: string) => void): void;
  on(event: "end", listener: () => void): void;
  setEncoding(encoding: "utf8"): void;
}

interface NodeRequest {
  destroy(error?: Error): void;
  end(body?: string): void;
  on(event: "error", listener: (error: Error) => void): void;
}

export function createCepFetch(): VidXPFetch {
  return (input, init = {}) => {
    const requestInit = init as RequestInit;
    const url = input instanceof Request ? input.url : String(input);
    const runtime = Reflect.get(window, "cep_node") as CepNodeRuntime | undefined;
    if (!runtime) return Promise.reject(new Error("Premiere's CEP network runtime is unavailable."));
    const protocol = new URL(url).protocol;
    const transport = runtime.require(protocol === "https:" ? "https" : "http");
    const headers = Object.fromEntries(new Headers(requestInit.headers).entries());

    return new Promise<Response>((resolve, reject) => {
      const request = transport.request(
        url,
        { headers, method: requestInit.method || "GET" },
        (response) => {
          response.setEncoding("utf8");
          let body = "";
          response.on("data", (chunk) => {
            body += chunk;
          });
          response.on("end", () => {
            const responseHeaders = new Headers();
            for (const [name, value] of Object.entries(response.headers)) {
              if (Array.isArray(value)) value.forEach((item) => responseHeaders.append(name, item));
              else if (value !== undefined) responseHeaders.set(name, value);
            }
            resolve(new Response(body, {
              headers: responseHeaders,
              status: response.statusCode || 500,
              statusText: response.statusMessage,
            }));
          });
        },
      );
      request.on("error", reject);
      requestInit.signal?.addEventListener("abort", () => request.destroy(abortError()), { once: true });
      request.end(typeof requestInit.body === "string" ? requestInit.body : undefined);
    });
  };
}

function abortError(): Error {
  const error = new Error("Operation cancelled");
  error.name = "AbortError";
  return error;
}
