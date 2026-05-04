import { describe, expect, it } from "vitest";
import { formatConnectionStatusLabel } from "./header-status";

describe("formatConnectionStatusLabel", () => {
  it("uses only connected state for fixed connection indicators", () => {
    expect(formatConnectionStatusLabel("PM-Market", true)).toBe("PM-Market已连通");
    expect(formatConnectionStatusLabel("PM-Sports", false)).toBe("PM-Sports断开连接");
  });

  it("does not expose enabled or disabled wording", () => {
    expect(formatConnectionStatusLabel("GS-WS", false)).toBe("GS-WS断开连接");
  });
});
