/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoutineConfigForm } from "@/components/routines/RoutineConfigForm";
import { ServerContext } from "@/hooks/useServer";
import type { RoutineFieldInfo } from "@/lib/api/routines";

const getRoutineFieldOptions = vi.fn();

vi.mock("@/lib/api/routines", () => ({
  routinesApi: {
    getRoutineFieldOptions: (...args: unknown[]) => getRoutineFieldOptions(...args),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;
const onChange = vi.fn();

async function render(
  fields: Record<string, RoutineFieldInfo>,
  initialValues: Record<string, unknown> = {},
) {
  function Form() {
    const [values, setValues] = useState(initialValues);
    return (
      <RoutineConfigForm
        fields={fields}
        values={values}
        onChange={(key, value) => {
          onChange(key, value);
          setValues((current) => ({ ...current, [key]: value }));
        }}
      />
    );
  }

  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ServerContext.Provider value={{ server: "local", setServer: () => {} }}>
          <Form />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
}

function control(labelText: string): HTMLElement {
  const label = [...container.querySelectorAll("label")].find(
    (item) => item.textContent === labelText,
  );
  expect(label).toBeTruthy();
  const element = document.getElementById(label!.htmlFor);
  expect(element).toBeTruthy();
  return element!;
}

async function typeNumber(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  getRoutineFieldOptions.mockResolvedValue({ options: ["available_config"] });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  container.remove();
  vi.clearAllMocks();
});

describe("RoutineConfigForm", () => {
  it("associates concise labels and help with controls, including the named switch", async () => {
    await render({
      start_date: { type: "str", default: "", description: "Start date in YYYY-MM-DD format." },
      send_chart: { type: "bool", default: false, description: "Send the chart image to the chat." },
    }, { send_chart: false });

    const date = control("Start Date");
    expect(document.getElementById(date.getAttribute("aria-describedby")!)?.textContent)
      .toBe("Start date in YYYY-MM-DD format.");

    const toggle = control("Send Chart");
    expect(toggle.getAttribute("role")).toBe("switch");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(document.getElementById(toggle.getAttribute("aria-describedby")!)?.textContent)
      .toBe("Send the chart image to the chat.");
    await act(async () => toggle.click());
    expect(onChange).toHaveBeenCalledWith("send_chart", true);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("keeps numeric drafts local until blur and restores the saved value for an empty draft", async () => {
    await render({
      trade_cost: { type: "float", default: 0.0002, description: "Trade cost as a decimal." },
    });
    const input = control("Trade Cost") as HTMLInputElement;

    await typeNumber(input, "0.00035");
    expect(input.value).toBe("0.00035");
    expect(onChange).not.toHaveBeenCalled();
    await act(async () => input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
    expect(onChange).toHaveBeenLastCalledWith("trade_cost", 0.00035);

    onChange.mockClear();
    await typeNumber(input, "");
    expect(input.value).toBe("");
    await act(async () => input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
    expect(input.value).toBe("0.00035");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("preserves a persisted selection and only initializes an unset select from loaded options", async () => {
    const field: RoutineFieldInfo = {
      type: "str", default: "", description: "Controller used for the backtest.",
      widget: "select", options_from: "controllers",
    };
    await render({ saved_controller: field, new_controller: field }, {
      saved_controller: "temporarily_unavailable_config",
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    // The label names a themed menu button, which keeps the persisted value.
    expect(control("Saved Controller").getAttribute("aria-haspopup")).toBe("listbox");
    expect(control("Saved Controller").textContent).toBe("temporarily_unavailable_config");
    expect(onChange).toHaveBeenCalledExactlyOnceWith("new_controller", "available_config");
  });

  it("offers a choice field's fixed options without loading any", async () => {
    await render({
      mode: { type: "Literal", default: "live", description: "Execution mode.", options: ["live", "mock"] },
    });

    const select = control("Mode");
    expect(select.textContent).toBe("live");
    await act(async () => select.click());
    const mock = [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent === "mock");
    await act(async () => (mock as HTMLElement).click());

    expect(onChange).toHaveBeenCalledExactlyOnceWith("mode", "mock");
    expect(getRoutineFieldOptions).not.toHaveBeenCalled();
  });
});
