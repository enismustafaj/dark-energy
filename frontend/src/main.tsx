import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Center,
  Container,
  createTheme,
  Group,
  Loader,
  MantineProvider,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  ThemeIcon,
  Title,
} from "@mantine/core";
import "@mantine/core/styles.css";
import {
  ArrowLeft,
  ArrowRight,
  Bolt,
  Bot,
  Home,
  MessageSquare,
  RotateCcw,
  Send,
  TrendingDown,
  TriangleAlert,
  X,
} from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { API_BASE_URL, getHouseholdView, listHouseholds, runAction, sendChatMessage, updateAdviceStatus } from "./api";
import "./styles.css";
import type { ActionEvent, Advice, ChatTurn, EnergyNode, Household, HouseholdView } from "./types";

const theme = createTheme({
  primaryColor: "energy",
  autoContrast: true,
  defaultRadius: "md",
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  headings: { fontFamily: "Inter, sans-serif", fontWeight: "600" },
  colors: {
    energy: ["#edfbf2", "#d3f5e0", "#a6ebc2", "#76e0a2", "#52d788", "#3ccf77", "#22c55e", "#16a34a", "#117a39", "#0c5e2c"],
  },
});

type Route = { name: "home" } | { name: "household"; householdId: string };

type ActiveSelection = { type: "all" } | { type: "contract" } | { type: "device"; deviceId: number };

type ActionOption = { type: string; label: string; factKey?: string };

type ChatMsg = {
  id: string;
  role: "user" | "agent";
  text: string;
  savings?: number | null;
  status?: string;
};

type ChatThread = {
  title: string;
  factKey?: string;
  resolved?: boolean;
  messages: ChatMsg[];
};

const GENERAL_THREAD_KEY = "general";

function agentGreeting(): ChatMsg {
  return {
    id: "greet",
    role: "agent",
    text: "Hi — I'm your HausWatt agent. Pick a recommendation to start a dedicated thread, or ask me about this household.",
  };
}

function parseRoute(): Route {
  const match = window.location.pathname.match(/^\/h\/([^/]+)$/);
  if (match) return { name: "household", householdId: decodeURIComponent(match[1]) };
  return { name: "home" };
}

function navigate(path: string): void {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function formatEuro(value: number | null | undefined): string {
  if (value == null) return "";
  return new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(value);
}

// Sentence-case a label: first letter upper, rest as-is, underscores to spaces.
function titleize(value: string): string {
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function msgId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

const CATEGORY_COLOR: Record<string, string> = {
  device_choice: "energy",
  contract: "blue",
  utilization: "yellow",
  fault: "red",
};

const SEVERITY_COLOR: Record<string, string> = {
  high: "red",
  warning: "yellow",
  info: "gray",
};

function accentVar(advice: Advice): string {
  const sev = SEVERITY_COLOR[advice.severity];
  const color = sev && sev !== "gray" ? sev : CATEGORY_COLOR[advice.category] ?? "gray";
  return `var(--mantine-color-${color}-6)`;
}

function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute());

  useEffect(() => {
    const onPop = () => setRoute(parseRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  return (
    <MantineProvider theme={theme} forceColorScheme="light">
      <Topbar />
      <Container size="lg" py="lg" className="app-main">
        {route.name === "home" ? <HouseholdPicker /> : <Dashboard householdId={route.householdId} />}
      </Container>
    </MantineProvider>
  );
}

function Topbar() {
  return (
    <Box component="header" className="topbar">
      <Container size="lg" h="100%">
        <Group h="100%" justify="space-between">
          <Group gap={10} className="brand" onClick={() => navigate("/")} role="button">
            <div className="logo-chip">
              <Bolt size={18} />
            </div>
            <div>
              <Text fw={600} fz={16} lh={1.2}>
                HausWatt
              </Text>
              <Text c="dimmed" fz={12}>
                Less cost. More loyalty. Zero disruption.
              </Text>
            </div>
          </Group>
        </Group>
      </Container>
    </Box>
  );
}

function HouseholdPicker() {
  const [households, setHouseholds] = useState<Household[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    listHouseholds()
      .then((items) => {
        if (!cancelled) {
          setHouseholds(items);
          setStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="baseline">
        <Title order={1} fz={26}>
          Households
        </Title>
        <Text c="dimmed" fz="sm">
          {status === "ready" ? `${households.length} active homes` : ""}
        </Text>
      </Group>

      {status === "loading" && (
        <Center py="xl">
          <Loader color="energy" />
        </Center>
      )}
      {status === "error" && (
        <Alert color="red" variant="light">
          Could not load households.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="md">
        {households.map((household) => (
          <Card
            key={household.household_id}
            className="hover-lift"
            withBorder
            padding="lg"
            radius="md"
            onClick={() => navigate(`/h/${household.household_id}`)}
            role="button"
          >
            <Group justify="space-between" align="flex-start">
              <Badge variant="light" color="energy" radius="sm">
                {household.household_id}
              </Badge>
              <ThemeIcon variant="transparent" color="gray" size="sm">
                <Home size={16} />
              </ThemeIcon>
            </Group>
            <Text fw={600} fz="lg" mt="sm">
              {household.name}
            </Text>
            <Text c="dimmed" fz="sm" mt={4}>
              {household.city} · {household.tariff_id} tariff
            </Text>
          </Card>
        ))}
      </SimpleGrid>
    </Stack>
  );
}

function Stat({ label, value, sub, subColor }: { label: string; value: React.ReactNode; sub?: React.ReactNode; subColor?: string }) {
  return (
    <Paper className="stat" radius="md" p="md">
      <Text fz={13} c="dimmed" mb={4}>
        {label}
      </Text>
      <Text fz={24} fw={600} lh={1.1}>
        {value}
      </Text>
      {sub != null && (
        <Text fz={12} c={subColor ?? "dimmed"} mt={3}>
          {sub}
        </Text>
      )}
    </Paper>
  );
}

function Dashboard({ householdId }: { householdId: string }) {
  const [view, setView] = useState<HouseholdView | null>(null);
  const [selection, setSelection] = useState<ActiveSelection>({ type: "all" });
  const [chatOpen, setChatOpen] = useState(false);
  const recsRef = useRef<HTMLDivElement>(null);
  const [activeThreadKey, setActiveThreadKey] = useState(GENERAL_THREAD_KEY);
  const [chatThreads, setChatThreads] = useState<Record<string, ChatThread>>(() => ({
    [GENERAL_THREAD_KEY]: {
      title: "Household chat",
      messages: [agentGreeting()],
    },
  }));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [resolvingFactKeys, setResolvingFactKeys] = useState<string[]>([]);
  const activeThreadKeyRef = useRef(activeThreadKey);

  useEffect(() => {
    activeThreadKeyRef.current = activeThreadKey;
  }, [activeThreadKey]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getHouseholdView(householdId)
      .then((data) => {
        if (!cancelled) {
          setView(data);
          setSelection({ type: "all" });
          setActiveThreadKey(GENERAL_THREAD_KEY);
          setChatThreads({
            [GENERAL_THREAD_KEY]: {
              title: "Household chat",
              messages: [agentGreeting()],
            },
          });
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load household.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [householdId]);

  // The household's still-open recommendations: everything the engine produced
  // minus what's already resolved or applied. This single list drives the CTA,
  // the counts, and the rendered recommendations so they always agree.
  const openAdvice = useMemo<Advice[]>(() => {
    const applied = new Set((view?.applied_advice ?? []).map((a) => a.fact_key));
    return (view?.advice ?? []).filter(
      (a) => a.status !== "resolved" && !applied.has(a.fact_key),
    );
  }, [view]);

  // Open advice filtered to the current selection (device / contract / all).
  const advice = useMemo<Advice[]>(() => {
    if (selection.type === "contract") return openAdvice.filter((a) => a.category === "contract");
    if (selection.type === "device") return openAdvice.filter((a) => a.device_id === selection.deviceId);
    return openAdvice; // unfiltered: show every open recommendation
  }, [openAdvice, selection]);

  // live action results stream back here and land in the chat as the agent's reply
  useEffect(() => {
    const stream = new EventSource(`${API_BASE_URL}/api/stream/${householdId}`);
    stream.addEventListener("action", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as ActionEvent;
      const threadKey = data.resolved_fact_key ?? activeThreadKeyRef.current;
      if (data.resolved_fact_key) {
        setView((prev) =>
          prev ? { ...prev, advice: prev.advice.filter((item) => item.fact_key !== data.resolved_fact_key) } : prev,
        );
        setChatThreads((prev) => {
          const thread = prev[data.resolved_fact_key as string];
          if (!thread) return prev;
          return {
            ...prev,
            [data.resolved_fact_key as string]: { ...thread, resolved: true },
          };
        });
      }
      pushMsg(threadKey, {
        role: "agent",
        text: data.message,
        savings: data.expected_savings_eur,
        status: data.status,
      });
      setActiveThreadKey(threadKey);
      setChatOpen(true);
    });
    return () => stream.close();
  }, [householdId]);

  // shift main content when the chat is docked
  useEffect(() => {
    document.body.classList.toggle("chat-open", chatOpen);
    return () => document.body.classList.remove("chat-open");
  }, [chatOpen]);

  const selectionLabel = useMemo(() => {
    if (!view || selection.type === "all") return "all devices";
    if (selection.type === "contract") return "contract";
    const node = view.nodes.find((item) => item.device_id === selection.deviceId);
    return node ? node.label : "device";
  }, [selection, view]);

  const actionOptions: ActionOption[] = useMemo(() => {
    const map = new Map<string, ActionOption>();
    for (const a of advice) {
      if (a.action_type && !map.has(a.action_type)) {
        map.set(a.action_type, { type: a.action_type, label: a.action_label || "Run action", factKey: a.fact_key });
      }
    }
    return Array.from(map.values());
  }, [advice]);

  const activeThread = chatThreads[activeThreadKey] ?? chatThreads[GENERAL_THREAD_KEY];
  const chatMessages = activeThread?.messages ?? [];
  const activeRecommendationFactKey = activeThread?.factKey;
  const activeThreadResolved = Boolean(activeThread?.resolved);

  function pushMsg(threadKey: string, m: Omit<ChatMsg, "id">) {
    setChatThreads((prev) => {
      const existing = prev[threadKey] ?? {
          title: threadKey === GENERAL_THREAD_KEY ? "Household chat" : "Recommendation",
          factKey: threadKey === GENERAL_THREAD_KEY ? undefined : threadKey,
          resolved: false,
          messages: threadKey === GENERAL_THREAD_KEY ? [agentGreeting()] : [],
        };
      return {
        ...prev,
        [threadKey]: {
          ...existing,
          messages: [...existing.messages, { ...m, id: msgId() }],
        },
      };
    });
  }

  const chatTurns = (messages: ChatMsg[]): ChatTurn[] =>
    messages.map((message) => ({ role: message.role, text: message.text }));

  async function runAgentAction(actionType: string, recommendationFactKey?: string) {
    try {
      await runAction(householdId, actionType, recommendationFactKey);
      // success message arrives via the SSE 'action' stream
    } catch (err) {
      pushMsg(recommendationFactKey ?? activeThreadKey, {
        role: "agent",
        text: err instanceof Error ? err.message : "That action isn't available.",
        status: "failed",
      });
    }
  }

  function recommendationSpec(item: Advice): string {
    const bits = [`Recommendation: ${item.title}.`, item.body];
    if (item.benefit_eur) bits.push(`Estimated impact: save about €${formatEuro(item.benefit_eur)}/yr.`);
    if (item.advice?.baseline_cost_eur != null && item.advice.counterfactual_cost_eur != null) {
      bits.push(
        `Current annual cost is about €${formatEuro(item.advice.baseline_cost_eur)} and the recommendation projects €${formatEuro(item.advice.counterfactual_cost_eur)}.`,
      );
    }
    if (item.advice?.payback_years) bits.push(`Expected payback is about ${Math.round(item.advice.payback_years)} years.`);
    return bits.join(" ");
  }

  function ensureRecommendationThread(item: Advice) {
    setChatThreads((prev) => {
      const existing = prev[item.fact_key];
      return {
        ...prev,
        [item.fact_key]: {
          title: item.action_label || item.title,
          factKey: item.fact_key,
          resolved: existing?.resolved ?? false,
          messages: existing?.messages ?? [],
        },
      };
    });
  }

  async function askAgent(message: string, recommendationFactKey?: string) {
    const threadKey = recommendationFactKey ?? GENERAL_THREAD_KEY;
    setChatOpen(true);
    setActiveThreadKey(threadKey);
    const history = chatTurns(chatThreads[threadKey]?.messages ?? []);
    pushMsg(threadKey, { role: "user", text: message });
    setChatBusy(true);
    try {
      const reply = await sendChatMessage(householdId, message, history, recommendationFactKey);
      pushMsg(threadKey, { role: "agent", text: reply.message });
    } catch (err) {
      pushMsg(threadKey, {
        role: "agent",
        text: err instanceof Error ? err.message : "The agent could not respond.",
        status: "failed",
      });
    } finally {
      setChatBusy(false);
    }
  }

  function handleRecommendation(item: Advice) {
    ensureRecommendationThread(item);
    askAgent(`Execute the mocked action for this recommendation now. Do not ask for confirmation.\n\n${item.title}\n\n${recommendationSpec(item)}`, item.fact_key)
      .then(() => {
        if (item.agent_actionable && item.action_type) {
          runAgentAction(item.action_type, item.fact_key);
        }
      });
  }

  async function handleResolveAdvice(item: Advice) {
    setResolvingFactKeys((prev) => [...prev, item.fact_key]);
    try {
      await updateAdviceStatus(householdId, item.fact_key, "resolved");
      setView((prev) => (prev ? { ...prev, advice: prev.advice.filter((entry) => entry.fact_key !== item.fact_key) } : prev));
      setChatThreads((prev) => {
        const existing = prev[item.fact_key];
        return {
          ...prev,
          [item.fact_key]: {
            title: existing?.title ?? item.title,
            factKey: item.fact_key,
            resolved: true,
            messages: existing?.messages ?? [],
          },
        };
      });
    } catch (err) {
      const threadKey = item.fact_key;
      ensureRecommendationThread(item);
      setChatOpen(true);
      setActiveThreadKey(threadKey);
      pushMsg(threadKey, {
        role: "agent",
        text: err instanceof Error ? err.message : "Could not resolve that recommendation.",
        status: "failed",
      });
    } finally {
      setResolvingFactKeys((prev) => prev.filter((factKey) => factKey !== item.fact_key));
    }
  }

  // called when the user types into the chat
  function handleChatSubmit(text: string) {
    const lc = text.toLowerCase();
    const match = actionOptions.find(
      (o) =>
        lc.includes(o.type.replace(/_/g, " ")) ||
        lc.includes(o.label.toLowerCase()) ||
        o.label.toLowerCase().split(/\s+/).some((w) => w.length > 3 && lc.includes(w)),
    );
    const threadFactKey = activeRecommendationFactKey ?? match?.factKey;
    askAgent(text, threadFactKey);
  }

  if (loading)
    return (
      <Center py="xl">
        <Loader color="energy" />
      </Center>
    );
  if (error || !view)
    return (
      <Alert color="red" variant="light">
        {error || "Household not found."}
      </Alert>
    );

  const hub = view.hub;

  const chatThreadList = Object.entries(chatThreads).map(([key, thread]) => ({
    key,
    title: thread.title,
    count: thread.messages.length,
    resolved: Boolean(thread.resolved),
  }));

  // Savings still on the table: open recommendations that carry a benefit. The
  // CTA's count matches the savings-bearing items so its number lines up with the
  // green "save €X/yr" badges in the list below.
  const savingAdvice = openAdvice.filter((a) => (a.benefit_eur ?? 0) > 0);
  const availableSavings = savingAdvice.reduce((sum, a) => sum + (a.benefit_eur ?? 0), 0);

  const focusRecommendations = () => {
    setSelection({ type: "all" });
    recsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <>
      <Stack gap="md">
        <Group justify="space-between" align="center" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Anchor c="dimmed" fz="sm" onClick={() => navigate("/")} component="button">
              <ArrowLeft size={16} />
            </Anchor>
            <div>
              <Title order={1} fz={20} lh={1.2}>
                {view.household.name}
              </Title>
              <Text c="dimmed" fz={12}>
                {view.household.household_id} · {view.household.city} · {selectionLabel}
              </Text>
            </div>
          </Group>
        </Group>

        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
          <Stat label="Annual cost" value={`€${formatEuro(hub?.annual_cost_eur)}`} sub="this year" />
          <Stat
            label="Monthly cost"
            value={`€${formatEuro(hub?.month_to_date_cost_eur)}`}
            sub="so far this month"
          />
          <Stat
            label="Est. end of month"
            value={`€${formatEuro(hub?.month_estimated_cost_eur)}`}
            sub="projected total"
          />
          <Stat
            label="Savings realized"
            value={`€${formatEuro(view.realized_savings_eur)}/yr`}
            sub={
              <Group gap={3} component="span">
                <TrendingDown size={12} /> from {view.applied_advice.length} applied
              </Group>
            }
            subColor={view.realized_savings_eur > 0 ? "energy.7" : "dimmed"}
          />
        </SimpleGrid>

        {availableSavings > 0 && (
          <Paper withBorder radius="lg" p="md" className="cta-banner">
            <Group justify="space-between" align="center" wrap="nowrap" gap="md">
              <Group gap="sm" wrap="nowrap">
                <ThemeIcon size={38} radius="md" color="energy" variant="light">
                  <Zap size={20} />
                </ThemeIcon>
                <div>
                  <Text fw={600} fz={15} lh={1.2}>
                    Unlock €{formatEuro(availableSavings)}/yr more
                  </Text>
                  <Text c="dimmed" fz={13}>
                    {savingAdvice.length} recommendation{savingAdvice.length === 1 ? "" : "s"} ready to apply
                  </Text>
                </div>
              </Group>
              <Button color="energy" radius="md" rightSection={<ArrowRight size={16} />} onClick={focusRecommendations} style={{ flexShrink: 0 }}>
                Review &amp; apply
              </Button>
            </Group>
          </Paper>
        )}

        <Paper withBorder radius="lg" p="md" className="flow-card">
          <EnergyScene view={view} selection={selection} onSelect={setSelection} />
        </Paper>

        <Group justify="space-between" align="center" mt={4} ref={recsRef} style={{ scrollMarginTop: 16 }}>
          <Text fz={15} fw={600}>
            {selection.type === "all" ? "Recommendations" : `${titleize(selectionLabel)} · Recommendations`}
          </Text>
          {selection.type !== "all" && (
            <Button variant="default" size="compact-sm" leftSection={<RotateCcw size={14} />} onClick={() => setSelection({ type: "all" })}>
              All devices
            </Button>
          )}
        </Group>
        <AdviceList
          advice={advice}
          onAction={handleRecommendation}
          onResolve={handleResolveAdvice}
          resolvingFactKeys={resolvingFactKeys}
          emptyAll={selection.type === "all"}
        />
      </Stack>

      <ChatPanel
        open={chatOpen}
        onOpen={() => {
          setActiveThreadKey(GENERAL_THREAD_KEY);
          setChatOpen(true);
        }}
        onClose={() => setChatOpen(false)}
        title={activeThread?.title ?? "Household chat"}
        threads={chatThreadList}
        activeThreadKey={activeThreadKey}
        onSelectThread={setActiveThreadKey}
        messages={chatMessages}
        onSubmit={handleChatSubmit}
        busy={chatBusy}
        disabled={activeThreadResolved}
      />
    </>
  );
}

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace";

const TAG_PILL: Record<string, { bg: string; fg: string }> = {
  pv: { bg: "#FAEEDA", fg: "#412402" },
  battery: { bg: "#EAF3DE", fg: "#173404" },
  heat_pump: { bg: "#EEEDFE", fg: "#26215C" },
  ev: { bg: "#E1F5EE", fg: "#04342C" },
  contract: { bg: "#EEF1F4", fg: "#2b2f36" },
};

function EnergyScene({
  view,
  selection,
  onSelect,
}: {
  view: HouseholdView;
  selection: ActiveSelection;
  onSelect: (selection: ActiveSelection) => void;
}) {
  const byCat = (c: string) => view.nodes.find((n) => n.category === c) ?? null;
  const pv = byCat("pv");
  const hp = byCat("heat_pump");
  const ev = byCat("ev");
  const extras = view.nodes.filter((n) => n.category === "battery" || n.category === "contract");

  const selectNode = (node: EnergyNode) => {
    if (node.kind === "contract") onSelect({ type: "contract" });
    else if (node.device_id != null) onSelect({ type: "device", deviceId: node.device_id });
  };

  const activeFor = (node: EnergyNode | null) => {
    if (!node) return false;
    if (node.kind === "contract") return selection.type === "contract";
    return selection.type === "device" && selection.deviceId === node.device_id;
  };

  const dim = (node: EnergyNode | null) => (selection.type === "all" ? 1 : activeFor(node) ? 1 : 0.3);
  const devClass = (node: EnergyNode | null) => `dev${activeFor(node) ? " sel" : ""}`;

  return (
    <>
      <svg className="scene" viewBox="0 0 680 470" width="100%" role="img" aria-label="Isometric home scene with solar, heat pump and EV">
        <defs>
          <path id="de-p-sun" d="M 578 70 Q 480 120 380 196" fill="none" />
          <path id="de-p-roof" d="M 356 232 Q 350 260 340 288" fill="none" />
          <path id="de-p-hp" d="M 282 308 Q 258 304 234 296" fill="none" />
          <path id="de-p-ev" d="M 317 310 Q 358 296 397 326" fill="none" />
        </defs>

        {ev && <polygon points="397,321 448,351 397,381 346,351" fill="#B4B2A9" opacity=".35" />}
        {hp && <polygon points="226,299 264,321 226,343 188,321" fill="#B4B2A9" opacity=".35" />}

        {pv && (
          <>
            <g className="de-tw">
              <circle cx="578" cy="60" r="18" fill="#EF9F27" />
              <circle cx="578" cy="60" r="11" fill="#FAC775" />
            </g>
            <g stroke="#EF9F27" strokeWidth="1.5" strokeLinecap="round" className="de-ray" fill="none">
              <line x1="578" y1="28" x2="578" y2="20" />
              <line x1="610" y1="60" x2="618" y2="60" />
              <line x1="601" y1="37" x2="606" y2="32" />
              <line x1="601" y1="83" x2="606" y2="88" />
              <line x1="555" y1="37" x2="550" y2="32" />
              <line x1="555" y1="83" x2="550" y2="88" />
              <line x1="546" y1="60" x2="538" y2="60" />
              <line x1="578" y1="92" x2="578" y2="100" />
            </g>
          </>
        )}

        <g className="home" onClick={() => onSelect({ type: "all" })} role="button" tabIndex={0}>
          <polygon points="416,244 378,178 302,222 340,288" fill="#D85A30" stroke="#993C1D" strokeWidth=".5" />
          <line x1="378" y1="178" x2="302" y2="222" stroke="#993C1D" strokeWidth="1" />
          <line x1="416" y1="244" x2="340" y2="288" stroke="#993C1D" strokeWidth=".5" opacity=".55" />
          <polygon points="264,244 302,222 340,288" fill="#D85A30" stroke="#993C1D" strokeWidth=".5" />
          <line x1="264" y1="244" x2="302" y2="222" stroke="#993C1D" strokeWidth="1" />
          <polygon points="416,310 340,354 340,288 416,244" fill="#D3D1C7" stroke="#888780" strokeWidth=".5" />
          <polygon points="264,310 340,354 340,288 264,244" fill="#F1EFE8" stroke="#888780" strokeWidth=".5" />
          <polygon points="302,292 315,300 315,340 302,332" fill="#993C1D" stroke="#712B13" strokeWidth=".5" />
          <circle cx="305" cy="320" r="1.3" fill="#FAEEDA" />
        </g>

        {pv && (
          <g className={devClass(pv)} opacity={dim(pv)} onClick={() => selectNode(pv)} role="button" tabIndex={0}>
            <g stroke="#0C447C" strokeWidth=".5">
              <polygon points="376,228 387,247 406,236 395,217" fill="#185FA5" />
              <polygon points="352,242 363,261 382,250 371,231" fill="#185FA5" />
              <polygon points="327,256 338,275 357,264 346,245" fill="#185FA5" />
              <polygon points="361,202 372,221 391,210 380,191" fill="#185FA5" />
              <polygon points="336,216 347,235 366,224 355,205" fill="#185FA5" />
              <polygon points="312,230 323,249 342,238 331,219" fill="#185FA5" />
            </g>
            <g stroke="#85B7EB" strokeWidth=".4" opacity=".55" fill="none">
              <line x1="391" y1="222" x2="396" y2="241" />
              <line x1="367" y1="236" x2="372" y2="255" />
              <line x1="343" y1="250" x2="347" y2="269" />
              <line x1="376" y1="196" x2="381" y2="215" />
              <line x1="351" y1="210" x2="356" y2="229" />
              <line x1="328" y1="224" x2="332" y2="243" />
            </g>
          </g>
        )}

        {hp && (
          <g className={devClass(hp)} opacity={dim(hp)} onClick={() => selectNode(hp)} role="button" tabIndex={0}>
            <polygon points="245,321 226,332 226,303 245,292" fill="#888780" stroke="#5F5E5A" strokeWidth=".5" />
            <polygon points="226,332 207,321 207,292 226,303" fill="#B4B2A9" stroke="#5F5E5A" strokeWidth=".5" />
            <polygon points="226,281 245,292 226,303 207,292" fill="#D3D1C7" stroke="#5F5E5A" strokeWidth=".5" />
            <ellipse cx="226" cy="292" rx="10" ry="5.5" fill="none" stroke="#5F5E5A" strokeWidth=".6" />
            <ellipse cx="226" cy="292" rx="6.5" ry="3.5" fill="none" stroke="#5F5E5A" strokeWidth=".5" />
            <ellipse cx="226" cy="292" rx="3" ry="1.6" fill="#5F5E5A" />
            <line x1="212" y1="307" x2="221" y2="313" stroke="#5F5E5A" strokeWidth=".4" />
            <line x1="212" y1="311" x2="221" y2="317" stroke="#5F5E5A" strokeWidth=".4" />
            <line x1="212" y1="315" x2="221" y2="321" stroke="#5F5E5A" strokeWidth=".4" />
          </g>
        )}

        {ev && (
          <g className={devClass(ev)} opacity={dim(ev)} onClick={() => selectNode(ev)} role="button" tabIndex={0}>
            <polygon points="445,349 397,376 397,361 445,333" fill="#0F6E56" stroke="#085041" strokeWidth=".5" />
            <polygon points="397,376 369,360 369,344 397,361" fill="#1D9E75" stroke="#085041" strokeWidth=".5" />
            <polygon points="416,317 445,333 397,361 369,344" fill="#5DCAA5" stroke="#085041" strokeWidth=".5" />
            <polygon points="380,342 401,354 401,346 380,333" fill="#1D9E75" stroke="#085041" strokeWidth=".5" />
            <polygon points="433,335 401,354 401,346 433,327" fill="#0F6E56" stroke="#085041" strokeWidth=".5" />
            <polygon points="412,314 433,327 401,345 380,333" fill="#9FE1CB" stroke="#085041" strokeWidth=".5" />
            <polygon points="384,338 398,346 398,343 384,335" fill="#444441" stroke="#085041" strokeWidth=".3" />
            <polygon points="428,331 408,343 408,340 428,328" fill="#444441" stroke="#085041" strokeWidth=".3" />
            <ellipse cx="380" cy="355" rx="1.6" ry="2.6" fill="#FCE9A6" stroke="#C9A23B" strokeWidth=".4" transform="rotate(-31 380 355)" />
            <ellipse cx="390" cy="361" rx="1.6" ry="2.6" fill="#FCE9A6" stroke="#C9A23B" strokeWidth=".4" transform="rotate(-31 390 361)" />
            <ellipse cx="402" cy="370" rx="2.5" ry="4.5" fill="#2C2C2A" />
            <ellipse cx="402" cy="370" rx="1.1" ry="2" fill="#888780" />
            <ellipse cx="442" cy="345" rx="2.5" ry="4.5" fill="#2C2C2A" />
            <ellipse cx="442" cy="345" rx="1.1" ry="2" fill="#888780" />
            <path d="M 317 308 Q 350 296 397 326" stroke="#2C2C2A" strokeWidth="1.6" fill="none" strokeLinecap="round" />
            <circle cx="397" cy="326" r="2.6" fill="#1D9E75" stroke="#085041" strokeWidth=".5" />
            <circle cx="317" cy="308" r="2.2" fill="#888780" stroke="#5F5E5A" strokeWidth=".4" />
          </g>
        )}

        {pv &&
          [0, 0.4, 0.8, 1.2, 1.6].map((b, i) => (
            <circle key={`s${i}`} r={i % 2 ? 2 : 2.5} fill="#BA7517" opacity={i % 2 ? 0.8 : 1}>
              <animateMotion dur="2s" repeatCount="indefinite" begin={`${b}s`}>
                <mpath href="#de-p-sun" />
              </animateMotion>
            </circle>
          ))}
        {pv &&
          [0, 0.55, 1.1].map((b, i) => (
            <circle key={`r${i}`} r={i === 1 ? 1.5 : 1.7} fill="#BA7517" opacity={i === 1 ? 0.8 : 1}>
              <animateMotion dur="1.6s" repeatCount="indefinite" begin={`${b}s`}>
                <mpath href="#de-p-roof" />
              </animateMotion>
            </circle>
          ))}
        {hp &&
          [0, 0.6, 1.2].map((b, i) => (
            <circle key={`h${i}`} r={i === 1 ? 1.6 : 1.8} fill="#7F77DD" opacity={i === 1 ? 0.8 : 1}>
              <animateMotion dur="1.8s" repeatCount="indefinite" begin={`${b}s`}>
                <mpath href="#de-p-hp" />
              </animateMotion>
            </circle>
          ))}
        {ev &&
          [0, 0.4, 0.9, 1.3].map((b, i) => (
            <circle key={`e${i}`} r={i % 2 ? 1.6 : 1.9} fill="#1D9E75" opacity={i % 2 ? 0.8 : 1}>
              <animateMotion dur="1.8s" repeatCount="indefinite" begin={`${b}s`}>
                <mpath href="#de-p-ev" />
              </animateMotion>
            </circle>
          ))}

        <g style={{ fontFamily: MONO, fontSize: 11 }}>
          {pv && (
            <>
              <line x1="378" y1="180" x2="430" y2="155" stroke="#cfcabf" strokeWidth=".5" />
              <text x="435" y="158" style={{ fill: "#185FA5", fontWeight: 600 }}>
                {pv.label}
              </text>
              <text x="435" y="172" style={{ fill: "#8a857c" }}>
                {pv.metric}
              </text>
            </>
          )}
          {hp && (
            <>
              <line x1="226" y1="282" x2="170" y2="252" stroke="#cfcabf" strokeWidth=".5" />
              <text x="150" y="250" textAnchor="end" style={{ fill: "#534AB7", fontWeight: 600 }}>
                {hp.label}
              </text>
              <text x="150" y="264" textAnchor="end" style={{ fill: "#8a857c" }}>
                {hp.metric}
              </text>
            </>
          )}
          {ev && (
            <>
              <line x1="445" y1="335" x2="500" y2="305" stroke="#cfcabf" strokeWidth=".5" />
              <text x="505" y="308" style={{ fill: "#0F6E56", fontWeight: 600 }}>
                {ev.label}
              </text>
              <text x="505" y="322" style={{ fill: "#8a857c" }}>
                {ev.metric}
              </text>
            </>
          )}
          <text x="340" y="392" textAnchor="middle" style={{ fill: "#3a3a37", fontWeight: 600 }}>
            Home
          </text>
          {view.hub && (
            <text x="340" y="406" textAnchor="middle" style={{ fill: "#8a857c" }}>
              €{formatEuro(view.hub.annual_cost_eur)}/yr
            </text>
          )}
        </g>
      </svg>

      {extras.length > 0 && (
        <Group gap={6} justify="center" mt={4}>
          {extras.map((node) => {
            const tag = TAG_PILL[node.category] ?? TAG_PILL.contract;
            const on = activeFor(node);
            return (
              <button
                key={node.category}
                type="button"
                className={`tag-pill${on ? " on" : ""}`}
                style={{ background: tag.bg, color: tag.fg }}
                onClick={() => selectNode(node)}
              >
                {node.icon} {node.label}
              </button>
            );
          })}
        </Group>
      )}
    </>
  );
}

function AdviceList({
  advice,
  onAction,
  onResolve,
  resolvingFactKeys,
  emptyAll,
}: {
  advice: Advice[];
  onAction: (advice: Advice) => void;
  onResolve: (advice: Advice) => void;
  resolvingFactKeys: string[];
  emptyAll?: boolean;
}) {
  if (!advice.length)
    return (
      <Text c="dimmed" fs="italic" fz="sm">
        {emptyAll
          ? "All caught up — every recommendation has been applied."
          : "No open recommendations for this selection."}
      </Text>
    );
  return (
    <Stack gap="sm">
      {advice.map((item) => (
        <Card key={item.fact_key} withBorder radius="md" padding="md" style={{ borderLeft: `3px solid ${accentVar(item)}` }}>
          <Group gap="xs" mb={6} wrap="wrap">
            <Badge size="xs" variant="light" color={CATEGORY_COLOR[item.category] ?? "gray"} radius="sm">
              {titleize(item.category)}
            </Badge>
            {item.benefit_eur ? (
              <Badge size="xs" variant="filled" color="energy" radius="sm">
                save €{item.benefit_eur}/yr
              </Badge>
            ) : null}
            {item.advice?.payback_years ? (
              <Text c="dimmed" fz="xs">
                ~{Math.round(item.advice.payback_years)}yr payback
              </Text>
            ) : null}
          </Group>
          <Title order={3} fz="md">
            {item.title}
          </Title>
          <Text c="dimmed" fz="sm" mt={4}>
            {item.body}
          </Text>
          {item.advice?.baseline_cost_eur != null && (
            <Group gap="xs" mt="sm" fz="sm" wrap="wrap">
              <Text c="dimmed" fz="sm">
                €{formatEuro(item.advice.baseline_cost_eur)}/yr now
              </Text>
              <ThemeIcon variant="transparent" color="energy" size="sm">
                <ArrowRight size={15} />
              </ThemeIcon>
              <Text fw={600} fz="sm">
                €{formatEuro(item.advice.counterfactual_cost_eur)}/yr
              </Text>
            </Group>
          )}
          <Group justify="flex-end" mt="sm">
            {item.agent_actionable ? (
              <Button size="sm" color="energy" leftSection={<MessageSquare size={15} />} onClick={() => onAction(item)}>
                {item.action_label || "Describe action needed"}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="default"
                onClick={() => onResolve(item)}
                loading={resolvingFactKeys.includes(item.fact_key)}
              >
                Resolve
              </Button>
            )}
          </Group>
        </Card>
      ))}
    </Stack>
  );
}

function ChatPanel({
  open,
  onOpen,
  onClose,
  title,
  threads,
  activeThreadKey,
  onSelectThread,
  messages,
  onSubmit,
  busy,
  disabled,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  title: string;
  threads: { key: string; title: string; count: number; resolved: boolean }[];
  activeThreadKey: string;
  onSelectThread: (threadKey: string) => void;
  messages: ChatMsg[];
  onSubmit: (text: string) => void;
  busy: boolean;
  disabled: boolean;
}) {
  const [text, setText] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [messages, open]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy || disabled) return;
    onSubmit(t);
    setText("");
  }

  return (
    <>
      <aside className={`chat-panel${open ? " open" : ""}`} aria-hidden={!open}>
        <div className="chat-head">
          <Group gap={8}>
            <div className="agent-chip">
              <Bot size={16} />
            </div>
            <div>
              <Text fw={600} fz={14} lh={1.1}>
                {title}
              </Text>
              <Text c="dimmed" fz={11}>
                HausWatt agent
              </Text>
            </div>
          </Group>
          <ActionIcon variant="subtle" color="gray" onClick={onClose} aria-label="Close chat">
            <X size={16} />
          </ActionIcon>
        </div>

        <div className="chat-shell">
          <div className="chat-main">
            <div className="chat-body" ref={bodyRef}>
              {messages.map((m) => (
                <div key={m.id} className={`bubble ${m.role}${m.status === "failed" ? " err" : ""}`}>
                  <div className="bubble-text">
                    {m.status === "failed" && <TriangleAlert size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />}
                    {m.text}
                  </div>
                  {m.savings && m.savings > 0 ? <div className="bubble-savings">~€{m.savings}/yr saved</div> : null}
                </div>
              ))}
              {busy && (
                <div className="bubble agent">
                  <div className="bubble-text">Thinking...</div>
                </div>
              )}
              {disabled && (
                <div className="bubble agent">
                  <div className="bubble-text">This recommendation is resolved. The thread is read-only.</div>
                </div>
              )}
            </div>

            <form className="chat-input" onSubmit={submit}>
              <TextInput
                value={text}
                onChange={(e) => setText(e.currentTarget.value)}
                placeholder={disabled ? "Resolved" : "Ask about this recommendation..."}
                disabled={busy || disabled}
                size="sm"
                radius="md"
                style={{ flex: 1 }}
              />
              <ActionIcon type="submit" size={36} radius="md" color="energy" variant="filled" aria-label="Send" loading={busy} disabled={disabled}>
                <Send size={16} />
              </ActionIcon>
            </form>
          </div>

          <div className="chat-threads">
            {threads.map((thread) => (
              <button
                key={thread.key}
                type="button"
                className={`chat-thread${thread.key === activeThreadKey ? " active" : ""}${thread.resolved ? " resolved" : ""}`}
                onClick={() => onSelectThread(thread.key)}
              >
                <span>{thread.title}</span>
                <small>{thread.resolved ? "done" : thread.count}</small>
              </button>
            ))}
          </div>
        </div>
      </aside>

      {!open && (
        <button type="button" className="chat-fab" onClick={onOpen} aria-label="Open agent chat">
          <MessageSquare size={20} />
        </button>
      )}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
