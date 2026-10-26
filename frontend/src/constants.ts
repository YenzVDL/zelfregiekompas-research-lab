export const PAGES = [
  "Overview",
  "Scenarios",
  "Question bank",
  "Snapshots",
  "Experiments",
  "Evaluation",
  "Metrics",
  "Models",
  "Protocol & records",
];

export const ICONS = ["◈", "▤", "◇", "▣", "⚗", "✓", "▥", "⬡", "☷"];

export const blankInputs = {
  learning_goal: null,
  planned_activity: null,
  progress: null,
  planned_time: null,
  reported_time: null,
  control_results: null,
  student_note: null,
  previous_question: null,
};

export const blankQuestion = {
  text: "What would you like to reflect on about this activity?",
  tags: [],
  conditions: { present: [], missing: [], contains: {} },
  priority: 0,
  origin: "Human-authored; unvalidated",
};

export const defaultSettings = {
  temperature: 0.2,
  seed: 42,
  num_predict: 128,
  num_ctx: 4096,
  top_p: 0.9,
  timeout_seconds: 120,
  think: false,
};

export const scenarioTopics = ["programming and algorithms", "databases and SQL", "web development and APIs", "software testing and Git", "networking and cybersecurity", "software design and requirements"];
export const scenarioVariations = ["completed with understanding", "partial completion", "incorrect control answer", "time deviation", "missing information", "contradictory information"];

export const emptyCriterion = () => ({
  value: null,
  explanation: "",
  context: "",
});
