import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { createFakeApi } from "./api";

describe("Case 1 face-attendance UI", () => {
  it("loads the dashboard and shows today's recent records", async () => {
    render(<App api={createFakeApi()} />);

    expect(await screen.findByRole("heading", { name: "人脸考勤" })).toBeVisible();
    expect(await screen.findByText("今日最近记录")).toBeVisible();
    expect(screen.getByText("林晓")).toBeVisible();
    expect(screen.getByText("周宁")).toBeVisible();
  });

  it("registers a user from a device capture", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: /用户管理/ }));
    await user.type(screen.getByLabelText("姓名"), "测试用户");
    await user.click(screen.getByRole("tab", { name: /设备摄像头/ }));
    await user.click(screen.getByRole("button", { name: "抓取画面" }));
    expect(await screen.findByText("已采集")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "注册用户" }));

    expect(await screen.findByText("用户注册成功")).toBeVisible();
    expect(await screen.findByText("测试用户")).toBeVisible();
  });

  it("releases an uploaded object URL when the capture is cleared", async () => {
    const user = userEvent.setup();
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    const api = createFakeApi();
    window.history.replaceState({}, "", "/users_page");
    render(<App api={api} />);

    const image = new File(["fake-image"], "portrait.jpg", { type: "image/jpeg" });
    await user.click(screen.getByRole("button", { name: /用户管理/ }));
    await user.upload(screen.getByLabelText("选择图像文件"), image);
    await user.click(screen.getByRole("button", { name: "重新采集" }));
    expect(revoke).toHaveBeenCalled();
    revoke.mockRestore();
  });

  it("submits a manual check-in from an uploaded image", async () => {
    const user = userEvent.setup();
    render(<App api={createFakeApi()} />);

    await user.click(screen.getByRole("button", { name: /考勤记录/ }));
    const image = new File(["fake-image"], "portrait.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择图像文件"), image);
    await user.click(screen.getByRole("button", { name: "提交打卡" }));

    expect(await screen.findByText(/打卡成功/)).toBeVisible();
    await waitFor(() => expect(screen.getAllByText("手动打卡").length).toBeGreaterThan(0));
  });

  it("surfaces a degraded runtime instead of claiming NPU readiness", async () => {
    const api = createFakeApi();
    api.health = async () => ({ status: "degraded", ready: false, camera_ready: false, error: "模型缺失" });
    window.history.replaceState({}, "", "/");
    render(<App api={api} />);

    expect((await screen.findAllByText("服务降级")).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("NPU READY")).not.toBeInTheDocument();
  });
});
