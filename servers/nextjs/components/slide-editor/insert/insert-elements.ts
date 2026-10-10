import type { TemplateV2InsertComponent } from "@/components/slide-editor/events/events";
import { normalizeChartTypeName } from "@/components/slide-editor/charts/chart-data";
import {
  measureNoWrapTextWidth,
  rawFont,
} from "@/components/slide-editor/text/template-v2-text";
import { measureMathLatex } from "@/lib/math";
import type {
  ChartType,
  Fill,
  Font,
  InfographicType,
  Marker,
  SlideElement,
  Stroke,
  TableCell,
} from "@/components/slide-editor/types";
import { INFOGRAPHIC_EXAMPLE_ICON_URLS } from "@/components/slide-editor/infographics/infographic-icons";
import { fitInfographicElementToData } from "@/components/slide-editor/infographics/infographic-sizing";
import {
  DEFAULT_TEMPLATE_THEME,
  templateThemeGraphColors,
  type TemplateTheme,
} from "@/lib/template-theme";

const DEFAULT_CHART_INSERT_POSITION = { x: 128, y: 115 };
const DEFAULT_CHART_INSERT_SIZE = { width: 717, height: 410 };
const DEFAULT_INFOGRAPHIC_INSERT_POSITION = { x: 128, y: 170 };
const DEFAULT_IMAGE_PLACEHOLDER_SRC = "/placeholder.jpg";
const DEFAULT_MATH_INSERT_CENTER_X = 640;
const DEFAULT_MATH_INSERT_CENTER_Y = 325;
const TEXT_INSERT_HORIZONTAL_PADDING_PX = 8;
const TEXT_INSERT_VERTICAL_PADDING_PX = 14;
const IMAGE_RADIUS = { tl: 10, tr: 10, bl: 10, br: 10 };
const MATH_INSERT_PRESETS: Record<
  string,
  { latex: string; name: string; fontSize?: number }
> = {
  equation: { latex: String.raw`E = mc^2`, name: "equation" },
  "equation-quadratic": {
    latex: String.raw`x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}`,
    name: "quadratic_formula",
    fontSize: 38,
  },
  "equation-summation": {
    latex: String.raw`\sum_{i=1}^{n} i = \frac{n(n+1)}{2}`,
    name: "summation_formula",
    fontSize: 40,
  },
  "equation-integral": {
    latex: String.raw`\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}`,
    name: "integral_formula",
    fontSize: 38,
  },
  "equation-matrix": {
    latex: String.raw`A = \begin{bmatrix} a & b \\ c & d \end{bmatrix}`,
    name: "matrix_formula",
    fontSize: 38,
  },
};

export type EditorInsertContent = {
  elements?: SlideElement[];
  components?: TemplateV2InsertComponent[];
};

function fittedTextHeight(
  lineCount: number,
  fontSize: number,
  lineHeight: number,
) {
  return Math.ceil(
    lineCount * fontSize * lineHeight + TEXT_INSERT_VERTICAL_PADDING_PX,
  );
}

function makeTextElement({
  name,
  text,
  x,
  y,
  width,
  height,
  size,
  color = "101323",
  bold = false,
  italic = false,
  lineHeight = 1.1,
  horizontal = "left",
}: {
  name: string;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  size: number;
  color?: string;
  bold?: boolean;
  italic?: boolean;
  lineHeight?: number;
  horizontal?: "left" | "center" | "right";
}): SlideElement {
  const maxLength = Math.max(1, text.trim().length);

  return {
    type: "text",
    position: { x, y },
    size: { width, height },
    alignment: { horizontal, vertical: "top" },
    runs: [{ text }],
    font: {
      family: "Arial",
      size,
      color,
      bold,
      italic,
      line_height: lineHeight,
    },
    decorative: false,
    name,
    max_length: maxLength,
    min_length: Math.ceil(maxLength / 2),
  };
}

function makeLatexTextElement({
  latex,
  name,
  fontSize = 44,
}: {
  latex: string;
  name: string;
  fontSize?: number;
}): SlideElement {
  const measured = measureMathLatex(latex, fontSize, true);
  const width = Math.max(
    1,
    Math.ceil(measured.width) + TEXT_INSERT_HORIZONTAL_PADDING_PX,
  );
  const height = Math.max(1, Math.ceil(measured.height));

  return {
    type: "text",
    position: {
      x: Math.round(DEFAULT_MATH_INSERT_CENTER_X - width / 2),
      y: Math.round(DEFAULT_MATH_INSERT_CENTER_Y - height / 2),
    },
    size: { width, height },
    alignment: { horizontal: "center", vertical: "middle" },
    font: {
      family: "KaTeX_Main",
      size: fontSize,
      color: "101323",
    },
    runs: [{ type: "latex", latex, display_mode: true }],
    decorative: false,
    name,
    max_length: 4000,
    min_length: 1,
  };
}

function makeTableCell({
  text,
  font,
  color,
  alignment = "left",
}: {
  text: string;
  font: Font;
  color?: Fill;
  alignment?: TableCell["alignment"];
}): TableCell {
  return {
    alignment,
    color,
    runs: [{ text, font }],
  };
}

function makeBulletListElement(marker: Marker): SlideElement {
  const baseFont = {
    size: 18,
    family: "Manrope",
    color: "#111111",
    bold: false,
    italic: false,
    line_height: 1.4,
    letter_spacing: 0,
    ellipsis: false,
  };
  const items = [
    [
      {
        text: "明确目标与受众",
        font: { ...baseFont, bold: true },
      },
    ],
    [{ text: "展示最有力的依据", font: { ...baseFont } }],
    [
      {
        text: "说明下一步行动",
        font: { ...baseFont, italic: true },
      },
    ],
  ];
  const renderFont = rawFont({ font: baseFont });
  const fittedWidth = Math.ceil(
    Math.max(
      ...items.map((item, index) => {
        const prefix =
          marker === "bullet"
            ? "• "
            : marker === "number"
              ? `${index + 1}. `
              : "";
        const text = item.map((run) => run.text).join("");
        return measureNoWrapTextWidth(`${prefix}${text}`, renderFont);
      }),
    ) + TEXT_INSERT_HORIZONTAL_PADDING_PX,
  );

  return {
    type: "text-list",
    position: { x: 122, y: 128 },
    size: {
      width: fittedWidth,
      height: fittedTextHeight(
        items.length,
        baseFont.size,
        baseFont.line_height,
      ),
    },
    rotation: 0,
    font: baseFont,

    marker,
    items,
    decorative: false,
    name:
      marker === "bullet"
        ? "bullet_list"
        : marker === "number"
          ? "numbered_list"
          : "list_item",
    max_items: 6,
    min_items: 3,
    max_item_length: 60,
    min_item_length: 30,
  };
}

function createDefaultTextInsertElements(kind?: string): SlideElement[] {
  const mathPreset = kind ? MATH_INSERT_PRESETS[kind] : undefined;
  if (mathPreset) return [makeLatexTextElement(mathPreset)];

  switch (kind) {
    case "title-block":
      return [
        makeTextElement({
          name: "slide_title",
          text: "在此输入幻灯片标题",
          x: 109,
          y: 109,
          width: 986,
          height: fittedTextHeight(1, 38, 1.1),
          size: 38,
          bold: true,
        }),
      ];
    case "subtitle":
      return [
        makeTextElement({
          name: "slide_subtitle",
          text: "在此输入简洁的副标题",
          x: 122,
          y: 154,
          width: 870,
          height: fittedTextHeight(1, 24, 1.2),
          size: 24,
          color: "344054",
          lineHeight: 1.2,
        }),
      ];
    case "bullet-list":
      return [makeBulletListElement("bullet")];
    case "numbered-list":
      return [makeBulletListElement("number")];
    case "list-item":
      return [makeBulletListElement("none")];
    case "quote":
      return [
        makeTextElement({
          name: "quote",
          text: "“在此输入引言或关键观点。”",
          x: 122,
          y: 147,
          width: 858,
          height: fittedTextHeight(2, 24, 1.25),
          size: 24,
          color: "101323",
          italic: true,
          lineHeight: 1.25,
        }),
      ];
    case "body-text":
      return [
        makeTextElement({
          name: "body_text",
          text: "在此输入正文，可用于简短段落或补充说明。",
          x: 122,
          y: 154,
          width: 858,
          height: fittedTextHeight(2, 18, 1.28),
          size: 18,
          color: "344054",
          lineHeight: 1.28,
        }),
      ];
    default:
      return [];
  }
}

function chartTypeFromPaletteId(id?: string): ChartType | null {
  const normalized = normalizeChartTypeName(id);
  switch (normalized) {
    case "area":
    case "bar":
    case "donut":
    case "horizontal_bar":
    case "line":
    case "pie":
    case "polar_area":
    case "radar":
    case "scatter":
      return normalized as ChartType;
    case "stackedbar":
    case "stacked_bar":
      return "stacked_bar";
    case "horizontalstackbar":
    case "horizontalstackedbar":
    case "horizontal_stack_bar":
    case "horizontal_stacked_bar":
      return "horizontal_stacked_bar";
    default:
      return null;
  }
}

function chartData(
  categories: string[],
  values: number[],
  colors: string[],
) {
  return categories.map((category, index) => ({
    label: category,
    value: values[index] ?? 0,
    color: colors[index % colors.length] ?? colors[0],
  }));
}

function chartExample(chartType: ChartType) {
  switch (chartType) {
    case "donut":
      return {
        title: "各客户群收入占比",
        categories: ["大型企业", "中型企业", "小型企业", "个人用户"],
        values: [42, 28, 18, 12],
        seriesName: "收入占比",
        colors: ["7F22FE", "155DFC", "F59E0B", "12B76A"],
      };
    case "horizontal_bar":
      return {
        title: "各渠道有效线索",
        categories: ["邮件", "搜索", "社交媒体", "转介绍"],
        values: [68, 54, 47, 38],
        seriesName: "有效线索",
        colors: ["7F22FE", "155DFC", "F59E0B", "12B76A"],
      };
    case "polar_area":
      return {
        title: "各优先级客服工单",
        categories: ["紧急", "高", "中", "低"],
        values: [18, 32, 46, 24],
        seriesName: "工单量",
        colors: ["7F22FE", "155DFC", "F59E0B", "12B76A"],
      };
    case "radar":
      return {
        title: "产品就绪度评分",
        categories: ["设计", "可靠性", "速度", "安全性", "易用性"],
        values: [82, 74, 88, 69, 91],
        seriesName: "就绪度评分",
        colors: ["7F22FE", "155DFC", "F59E0B", "12B76A", "06B6D4"],
      };
    case "scatter":
      return {
        title: "营销活动转化结果",
        categories: ["活动甲", "活动乙", "活动丙", "活动丁"],
        values: [42, 57, 63, 76],
        seriesName: "转化量",
        colors: ["7F22FE", "155DFC", "F59E0B", "12B76A"],
      };
    default:
      return {
        title: "季度收入趋势",
        categories: ["Q1", "Q2", "Q3", "Q4"],
        values: [38, 54, 47, 68],
        seriesName: "收入",
        colors: ["7F22FE", "155DFC", "F59E0B", "12B76A"],
      };
  }
}

function makeChartElement(chartType: ChartType): SlideElement {
  const schema = {
    decorative: false,
    name: `${chartType}_chart`,
  };

  if (chartType === "bar") {
    const categories = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
    const values = [70, 120, 45, 145, 105, 105, 45];
    const colors = [
      "4D20C5",
      "155DFC",
      "F59E0B",
      "12B76A",
      "EF4444",
      "06B6D4",
      "8B5CF6",
    ];

    return {
      type: "chart",
      position: { ...DEFAULT_CHART_INSERT_POSITION },
      size: { ...DEFAULT_CHART_INSERT_SIZE },
      chart_type: "bar",
      title: "网站每周访问量\n6 月 10—16 日",
      color: "4D20C5",
      axis_color: "D8D8D8",
      grid_color: "D8D8D8",
      data_labels: "top",
      x_axis_grid: true,
      y_axis_grid: true,
      x_axis: true,
      y_axis: true,
      categories,
      series: [{ name: "访问量", values }],
      colors,
      data: chartData(categories, values, colors),
      ...schema,
    };
  }

  if (chartType === "line") {
    const categories = ["2021", "2022", "2023", "2024", "2025", "2026"];
    const values = [15, 45, 85, 50, 75, 95];
    const colors = [
      "4D20C5",
      "155DFC",
      "F59E0B",
      "12B76A",
      "EF4444",
      "06B6D4",
    ];

    return {
      type: "chart",
      position: { ...DEFAULT_CHART_INSERT_POSITION },
      size: { ...DEFAULT_CHART_INSERT_SIZE },
      chart_type: "line",
      title: "收入增长\n2021—2026 年",
      color: "4D20C5",
      axis_color: "D8D8D8",
      grid_color: "D8D8D8",
      data_labels: null,
      x_axis_grid: true,
      y_axis_grid: true,
      x_axis: true,
      y_axis: false,
      categories,
      series: [{ name: "收入", values }],
      colors,
      data: chartData(categories, values, colors),
      ...schema,
    };
  }

  if (chartType === "area") {
    const categories = ["2021", "2022", "2023", "2024", "2025", "2026"];
    const values = [25, 48, 46, 57, 62, 78];
    const colors = [
      "4D20C5",
      "155DFC",
      "F59E0B",
      "12B76A",
      "EF4444",
      "06B6D4",
    ];

    return {
      type: "chart",
      position: { ...DEFAULT_CHART_INSERT_POSITION },
      size: { ...DEFAULT_CHART_INSERT_SIZE },
      chart_type: "area",
      title: "月活跃用户\n2021—2026 年",
      color: "7555F6",
      axis_color: "D8D8D8",
      grid_color: "D8D8D8",
      data_labels: null,
      x_axis_grid: true,
      y_axis_grid: true,
      x_axis: true,
      y_axis: false,
      categories,
      series: [{ name: "活跃用户", values }],
      colors,
      data: chartData(categories, values, colors),
      ...schema,
    };
  }

  if (chartType === "pie") {
    const categories = ["产品", "服务", "支持", "培训"];
    const values = [45, 30, 15, 10];
    const colors = ["7555F6", "155DFC", "F59E0B", "12B76A"];

    return {
      type: "chart",
      position: { ...DEFAULT_CHART_INSERT_POSITION },
      size: { ...DEFAULT_CHART_INSERT_SIZE },
      chart_type: "pie",
      title: "各业务收入构成",
      color: "7555F6",
      axis_color: "D8D8D8",
      grid_color: "D8D8D8",
      data_labels: "top",
      categories,
      series: [{ name: "收入占比", values }],
      colors,
      data: chartData(categories, values, colors),
      ...schema,
    };
  }

  if (chartType === "stacked_bar" || chartType === "horizontal_stacked_bar") {
    const categories = ["Q1", "Q2", "Q3", "Q4"];
    const values = [38, 54, 47, 68];
    const secondaryValues = [24, 36, 31, 42];
    const colors = ["7F22FE", "155DFC"];

    return {
      type: "chart",
      position: { ...DEFAULT_CHART_INSERT_POSITION },
      size: { width: 538, height: 410 },
      chart_type: chartType,
      title: "各业务季度收入",
      color: "7F22FE",
      axis_color: "D0D5DD",
      grid_color: "D0D5DD",
      data_labels: "top",
      legend: true,
      x_axis_grid: true,
      y_axis_grid: true,
      categories,
      series: [
        { name: "新业务", values },
        { name: "增购业务", values: secondaryValues },
      ],
      colors,
      data: chartData(categories, values, colors),
      ...schema,
    };
  }

  const { title, categories, values, seriesName, colors } =
    chartExample(chartType);

  return {
    type: "chart",
    position: { ...DEFAULT_CHART_INSERT_POSITION },
    size: { width: 538, height: 410 },
    chart_type: chartType,
    title,
    color: "7F22FE",
    axis_color: "D0D5DD",
    grid_color: "D0D5DD",
    data_labels: "top",
    x_axis_grid: true,
    y_axis_grid: true,
    categories,
    series: [{ name: seriesName, values }],
    colors,
    data: chartData(categories, values, colors),
    ...schema,
  };
}

function createDefaultChartInsertElements(kind?: string): SlideElement[] {
  const chartType = chartTypeFromPaletteId(kind);
  return chartType ? [makeChartElement(chartType)] : [];
}

function infographicTypeFromPaletteId(id?: string): InfographicType | null {
  switch (id) {
    case "progress_bar":
    case "progress-bar":
      return "progress_bar";
    case "gauge":
    case "gauge-chart":
      return "gauge";
    case "gantt":
    case "gantt-chart":
      return "gantt";
    case "timeline":
      return "timeline";
    case "roadmap":
    case "road-map":
      return "roadmap";
    case "milestone_timeline":
    case "milestone-timeline":
      return "milestone_timeline";
    case "staircase":
    case "staircase-steps":
      return "staircase";
    case "supply_chain":
    case "supply-chain":
      return "supply_chain";
    case "stair_step_blocks":
    case "stair-step-blocks":
      return "stair_step_blocks";
    case "maturity_model":
    case "maturity-model":
      return "maturity_model";
    case "pillar_framework":
    case "pillar-framework":
      return "pillar_framework";
    case "transformation_hub":
    case "transformation-hub":
      return "transformation_hub";
    case "diagonal_circles":
    case "diagonal-circles":
      return "diagonal_circles";
    case "risk_matrix":
    case "risk-matrix":
      return "risk_matrix";
    case "chevron_process":
    case "chevron-process":
      return "chevron_process";
    case "radial_cycle":
    case "radial-cycle":
      return "radial_cycle";
    case "conversion_funnel":
    case "conversion-funnel":
      return "conversion_funnel";
    case "vertical_funnel":
    case "vertical-funnel":
      return "vertical_funnel";
    case "pyramid":
      return "pyramid";
    case "segmented_wheel":
    case "segmented-wheel":
      return "segmented_wheel";
    case "customer_journey":
    case "customer-journey":
      return "customer_journey";
    case "before_after":
    case "before-after":
      return "before_after";
    case "impact_effort_matrix":
    case "impact-effort-matrix":
      return "impact_effort_matrix";
    case "comparison_matrix":
    case "comparison-matrix":
      return "comparison_matrix";
    case "org_chart":
    case "org-chart":
      return "org_chart";
    case "decision_tree":
    case "decision-tree":
      return "decision_tree";
    case "mind_map":
    case "mind-map":
      return "mind_map";
    default:
      return null;
  }
}

function makeInfographicElement(infographicType: InfographicType): SlideElement {
  if (infographicType === "progress_bar") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 420, height: 74 },
      data: {
        type: "progress_bar",
        min_value: 0,
        max_value: 100,
        value: 68,
      },
      colors: ["E5E7EB", "2563EB"],
      text_color: "111111",
      decorative: false,
      name: "progress_bar",
    };
  }

  if (infographicType === "gantt") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 300 },
      data: {
        type: "gantt",
        columns: ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月"].map((label) => ({
          label,
        })),
        rows: [
          {
            label: "调研与发现",
            items: [
              {
                name: "调研与发现",
                start: { column: 0, offset: 0 },
                end: { column: 2, offset: 0.5 },
              },
            ],
          },
          {
            label: "内容规划",
            items: [
              {
                name: "内容规划",
                start: { column: 1, offset: 0.14 },
                end: { column: 2, offset: 0.88 },
              },
            ],
          },
          {
            label: "策略制定",
            items: [
              {
                name: "策略制定",
                start: { column: 2, offset: 0.34 },
                end: { column: 4, offset: 0.86 },
              },
            ],
          },
          {
            label: "设计与制作",
            items: [{
              name: "设计与制作",
              start: { column: 4, offset: 0 },
              end: { column: 6, offset: 0.38 },
            }],
          },
          {
            label: "内容创作",
            items: [{
              name: "内容创作",
              start: { column: 5, offset: 0 },
              end: { column: 7, offset: 1 },
            }],
          },
          {
            label: "活动上线",
            items: [{
              name: "活动上线",
              start: { column: 5, offset: 0.52 },
              end: { column: 7, offset: 0.68 },
            }],
          },
          {
            label: "效果跟踪",
            items: [{
              name: "效果跟踪",
              start: { column: 4, offset: 0.3 },
              end: { column: 7, offset: 1 },
            }],
          },
          {
            label: "优化",
            items: [{
              name: "优化",
              start: { column: 6, offset: 0.9 },
              end: { column: 7, offset: 1 },
            }],
          },
          {
            label: "最终复核",
            items: [{
              name: "最终复核",
              start: { column: 6, offset: 0.56 },
              end: { column: 7, offset: 1 },
            }],
          },
        ],
      },
      colors: [
        "FFFFFF",
        "102E79",
        "24468E",
        "385EAA",
        "4D73BE",
        "6388D0",
        "7CA2E5",
        "9AC8ED",
        "B5DCF4",
        "CBEAF7",
      ],
      text_color: "111111",
      decorative: false,
      name: "gantt_chart",
    };
  }

  if (infographicType === "timeline") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 260 },
      data: {
        type: "timeline",
        items: [
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover,
              color: "FFFFFF",
            },
            heading: "发现",
            description: "调研问题并识别关键需求。",
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define,
              color: "FFFFFF",
            },
            heading: "定义",
            description: "明确目标、优先级与方向。",
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.plan,
              color: "FFFFFF",
            },
            heading: "规划",
            description: "制定策略、时间线和行动计划。",
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute,
              color: "FFFFFF",
            },
            heading: "执行",
            description: "落实计划并跟踪进度。",
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.measure,
              color: "FFFFFF",
            },
            heading: "评估",
            description: "按关键绩效指标复核结果。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0"],
      text_color: "111111",
      decorative: false,
      name: "timeline",
    };
  }

  if (infographicType === "roadmap") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 252 },
      data: {
        type: "roadmap",
        items: [
          {
            heading: "发现",
            description: "调研问题并识别关键需求。",
          },
          {
            heading: "定义",
            description: "明确目标、优先级与方向。",
          },
          {
            heading: "规划",
            description: "制定策略、时间线和行动计划。",
          },
          {
            heading: "执行",
            description: "落实计划并跟踪进度。",
          },
          {
            heading: "评估",
            description: "按关键绩效指标复核结果。",
          },
          {
            heading: "复盘",
            description: "总结经验并确定下一步方向。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "7CA2E5"],
      text_color: null,
      decorative: false,
      name: "roadmap",
    };
  }

  if (infographicType === "milestone_timeline") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 260 },
      data: {
        type: "milestone_timeline",
        items: Array.from({ length: 7 }, (_, index) => ({
          heading: String(2020 + index),
          description: "明确项目愿景、目标与战略方向。",
        })),
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "7CA2E5", "D6D6D6"],
      text_color: null,
      decorative: false,
      name: "milestone_timeline",
    };
  }

  if (infographicType === "staircase") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 340 },
      data: {
        type: "staircase",
        items: [
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover, color: "FFFFFF" },
            heading: "推动增长",
            description: "扩大客户覆盖，提升参与度与收入。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define, color: "FFFFFF" },
            heading: "进入市场",
            description: "有策略地推出产品并建立市场影响力。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.plan, color: "FFFFFF" },
            heading: "构建方案",
            description: "开发产品、服务与用户体验。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute, color: "FFFFFF" },
            heading: "了解市场",
            description: "识别客户需求、市场趋势与竞争空白。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.measure, color: "FFFFFF" },
            heading: "明确愿景",
            description: "明确目标、优先级与长期方向。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0"],
      text_color: null,
      decorative: false,
      name: "staircase",
    };
  }

  if (infographicType === "supply_chain") {
    const entries = [
      ["寻源", "原材料\n供应商网络"],
      ["采购", "采购\n质量与成本"],
      ["生产", "制造\n质量控制"],
      ["配送", "仓储\n物流与交付"],
      ["终端市场", "零售／企业\n客户交付"],
    ] as const;
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 300 }, data: { type: "supply_chain", items: entries.map(([heading, description], index) => ({ icon: { url: Object.values(INFOGRAPHIC_EXAMPLE_ICON_URLS)[index % 5], color: "FFFFFF" }, heading, description })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"], text_color: null, decorative: false, name: "supply_chain" };
  }

  if (infographicType === "stair_step_blocks") {
    const entries = [
      ["基础", "建立核心能力、清晰流程、资源和运营架构。"],
      ["优化", "简化流程、提高效率、减少瓶颈并改善绩效。"],
      ["扩张", "扩充产能、客户覆盖、技术与运营能力，支撑增长。"],
      ["加速", "投入创新、新机会、合作伙伴及高影响力增长项目。"],
      ["引领", "通过持续改进与差异化建立市场领导力。"],
    ] as const;
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 350 }, data: { type: "stair_step_blocks", items: entries.map(([heading, description], index) => ({ icon: { url: Object.values(INFOGRAPHIC_EXAMPLE_ICON_URLS)[index % 5], color: "FFFFFF" }, heading, description })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0"], text_color: null, decorative: false, name: "stair_step_blocks" };
  }

  if (infographicType === "maturity_model") {
    const entries = [
      ["初始", "通过自动化、创新、高级分析和主动绩效管理持续提升能力。"],
      ["发展中", "通过绩效指标和数据衡量表现，帮助团队识别差距并管理结果。"],
      ["已规范", "核心流程已标准化并形成文档，执行一致，职责与治理更清晰。"],
      ["已管理", "基本流程和职责逐步建立，但团队间执行方式仍不一致。"],
      ["已优化", "流程仍以非正式、被动应对为主，缺少标准化与明确责任。"],
    ] as const;
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 390 }, data: { type: "maturity_model", items: entries.map(([heading, description], index) => ({ icon: { url: Object.values(INFOGRAPHIC_EXAMPLE_ICON_URLS)[index % 5], color: "FFFFFF" }, heading, description })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0"], text_color: null, decorative: false, name: "maturity_model" };
  }

  if (infographicType === "pillar_framework") {
    const entries = [
      ["客户", "加强客户关系、改善体验，在关键触点提供更大价值。", "体验与价值"],
      ["增长", "扩大市场覆盖、开拓新机会，建立可持续收入来源。", "收入与市场"],
      ["运营", "简化流程、提高生产力，建立可扩展的运营能力。", "效率与规模"],
      ["创新", "运用技术、数据与创新打造新方案和差异化优势。", "数字化与创新"],
      ["人才", "培养能力、提升领导力，形成担当与持续改进的文化。", "人才与文化"],
    ] as const;
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 380 }, data: { type: "pillar_framework", title: "增长与转型框架", items: entries.map(([heading, description, focus], index) => ({ icon: { url: Object.values(INFOGRAPHIC_EXAMPLE_ICON_URLS)[index % 5], color: "FFFFFF" }, heading, description, focus })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"], text_color: null, decorative: false, name: "pillar_framework" };
  }

  if (infographicType === "transformation_hub") {
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 300 }, data: { type: "transformation_hub", center_label: "业务\n转型", items: ["战略", "客户", "人才", "流程", "技术", "数据"].map((heading) => ({ heading })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"], text_color: null, decorative: false, name: "transformation_hub" };
  }

  if (infographicType === "diagonal_circles") {
    const entries = [
      ["战略", "明确组织方向、优先级、目标及投资重点。"],
      ["人才", "建设执行战略所需的技能、领导力、责任机制和文化。"],
      ["客户", "聚焦客户需求、体验、参与度与价值创造。"],
      ["流程", "改善工作流程、标准化、效率与治理。"],
      ["技术", "通过系统、自动化、集成与数据能力推动数字化转型。"],
    ] as const;
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 430 }, data: { type: "diagonal_circles", items: entries.map(([heading, description], index) => ({ icon: { url: Object.values(INFOGRAPHIC_EXAMPLE_ICON_URLS)[index % 5], color: "FFFFFF" }, heading, description })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"], text_color: null, decorative: false, name: "diagonal_circles" };
  }

  if (infographicType === "risk_matrix") {
    const entries = [
      ["识别", "识别运营、技术、财务、人员及外部因素中的潜在风险。"],
      ["排序", "按严重程度排列风险，将资源集中于最关键领域。"],
      ["评估", "评估发生概率、影响及暴露程度，了解各项风险。"],
      ["应对", "为重点风险制定缓解措施、控制办法、责任人和应急预案。"],
    ] as const;
    return { type: "infographic", position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION }, size: { width: 720, height: 370 }, data: { type: "risk_matrix", center_label: "风险", items: entries.map(([heading, description], index) => ({ icon: { url: Object.values(INFOGRAPHIC_EXAMPLE_ICON_URLS)[index % 5], color: "FFFFFF" }, heading, description })) }, colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"], text_color: null, decorative: false, name: "risk_matrix" };
  }

  if (infographicType === "chevron_process") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 360 },
      data: {
        type: "chevron_process",
        items: [
          {
            heading: "发现",
            description: "了解业务、市场及关键挑战。",
          },
          {
            heading: "分析",
            description: "分析数据、趋势、竞争对手与机会。",
          },
          {
            heading: "定义",
            description: "确定战略议题优先级并明确目标。",
          },
          {
            heading: "设计",
            description: "制定解决方案、行动计划与战略重点。",
          },
          {
            heading: "实施",
            description: "制定路线图、行动、负责人和下一步安排。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0"],
      text_color: null,
      decorative: false,
      name: "chevron_process",
    };
  }

  if (infographicType === "radial_cycle") {
    return {
      type: "infographic",
      position: { x: 250, y: 100 },
      size: { width: 560, height: 520 },
      data: {
        type: "radial_cycle",
        center_image:
          "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=900&q=80",
        items: [
          {
            heading: "发现",
            description: "了解业务、市场及关键挑战。",
          },
          {
            heading: "分析",
            description: "复核数据、识别规律并评估机会。",
          },
          {
            heading: "规划",
            description: "确定优先级、制定方法并明确行动。",
          },
          {
            heading: "执行",
            description: "落实建议措施并跟踪进度。",
          },
          {
            heading: "优化",
            description: "衡量绩效并改进计划。",
          },
          {
            heading: "收尾",
            description: "记录结果、决策与下一步行动。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "7CA2E5"],
      text_color: null,
      decorative: false,
      name: "radial_cycle",
    };
  }

  if (infographicType === "conversion_funnel") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 320 },
      data: {
        type: "conversion_funnel",
        items: [
          {
            value: 57,
            heading: "认知",
            description: "落实建议措施并跟踪进度。",
          },
          {
            value: 42,
            heading: "兴趣",
            description: "用户开始关注产品或服务。",
          },
          {
            value: 36,
            heading: "考虑",
            description: "潜在客户正在评估方案。",
          },
          {
            value: 27,
            heading: "意向",
            description: "用户表现出强烈购买意向。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "7CA2E5"],
      text_color: null,
      decorative: false,
      name: "conversion_funnel",
    };
  }

  if (infographicType === "vertical_funnel") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 480 },
      data: {
        type: "vertical_funnel",
        items: [
          {
            value: 100,
            heading: "认知",
            description: "进入漏斗的全部受众。",
          },
          {
            value: 60,
            heading: "兴趣",
            description: "对产品或服务产生兴趣的人群。",
          },
          {
            value: 35,
            heading: "考虑",
            description: "潜在客户正在评估方案。",
          },
          {
            value: 20,
            heading: "转化",
            description: "完成目标行动的人群。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "4D73BE", "7CA2E5"],
      text_color: null,
      decorative: false,
      name: "vertical_funnel",
    };
  }

  if (infographicType === "pyramid") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 400 },
      data: {
        type: "pyramid",
        items: [
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover, color: "FFFFFF" },
            heading: "基础",
            description: "建立稳健的流程、能力和资源。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define, color: "FFFFFF" },
            heading: "效率",
            description: "精简运营、弥补差距并优化成本。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.plan, color: "FFFFFF" },
            heading: "增长",
            description: "进入新市场、强化产品服务并扩大覆盖。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute, color: "FFFFFF" },
            heading: "创新",
            description: "开发新理念、新方案与竞争优势。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE"],
      text_color: null,
      decorative: false,
      name: "pyramid",
    };
  }

  if (infographicType === "segmented_wheel") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 460 },
      data: {
        type: "segmented_wheel",
        items: [
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.measure, color: "FFFFFF" },
            heading: "基础",
            description: "建立稳健的流程、能力和资源。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define, color: "FFFFFF" },
            heading: "效率",
            description: "精简运营、弥补差距并优化成本。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute, color: "FFFFFF" },
            heading: "增长",
            description: "进入新市场、强化产品服务并扩大覆盖。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.plan, color: "FFFFFF" },
            heading: "创新",
            description: "开发新理念、新方案与竞争优势。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover, color: "FFFFFF" },
            heading: "领导力",
            description: "建立长期韧性、市场领导力与价值创造能力。",
          },
        ],
      },
      colors: ["FFFFFF", "24468E", "385EAA", "4D73BE", "6388D0", "102E79"],
      text_color: null,
      decorative: false,
      name: "segmented_wheel",
    };
  }

  if (infographicType === "customer_journey") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 420 },
      data: {
        type: "customer_journey",
        items: [
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover, color: "111111" },
            heading: null,
            description: null,
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.measure, color: "FFFFFF" },
            heading: "认知",
            description: "客户通过营销与推荐了解品牌。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover, color: "FFFFFF" },
            heading: "考虑",
            description: "客户探索选项并比较替代方案。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define, color: "FFFFFF" },
            heading: "购买",
            description: "客户做出决定并完成交易。",
          },
          {
            icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute, color: "FFFFFF" },
            heading: "体验",
            description: "客户使用产品或服务，并与支持团队互动。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"],
      text_color: null,
      decorative: false,
      name: "customer_journey",
    };
  }

  if (infographicType === "before_after") {
    const beforeItems = [
      ["手工流程", "工作流程耗时较长。"],
      ["信息不透明", "数据获取困难。"],
      ["流程割裂", "团队各自为政。"],
    ] as const;
    const afterItems = [
      ["自动化流程", "运营更快、更顺畅。"],
      ["更快决策", "更快地依据数据采取行动。"],
      ["流程协同", "团队协作更紧密。"],
    ] as const;
    const beforeIcons = [
      INFOGRAPHIC_EXAMPLE_ICON_URLS.measure,
      INFOGRAPHIC_EXAMPLE_ICON_URLS.discover,
      INFOGRAPHIC_EXAMPLE_ICON_URLS.define,
    ];
    const afterIcons = [
      INFOGRAPHIC_EXAMPLE_ICON_URLS.plan,
      INFOGRAPHIC_EXAMPLE_ICON_URLS.execute,
      INFOGRAPHIC_EXAMPLE_ICON_URLS.measure,
    ];
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 460 },
      data: {
        type: "before_after",
        before_label: "改进前",
        after_label: "改进后",
        items: beforeItems.flatMap(([beforeHeading, beforeDescription], index) => [
          {
            icon: { url: beforeIcons[index], color: "FFFFFF" },
            heading: beforeHeading,
            description: beforeDescription,
          },
          {
            icon: { url: afterIcons[index], color: "FFFFFF" },
            heading: afterItems[index][0],
            description: afterItems[index][1],
          },
        ]),
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "7CA2E5"],
      text_color: null,
      decorative: false,
      name: "before_after",
    };
  }

  if (infographicType === "impact_effort_matrix") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 420 },
      data: {
        type: "impact_effort_matrix",
        x_axis_label: "影响",
        y_axis_label: "投入",
        low_label: "低",
        high_label: "高",
        items: [
          {
            heading: "快速见效",
            description: "影响大且投入较少的举措，适合立即执行并快速取得成果。",
          },
          {
            heading: "战略重点",
            description: "影响大但需要大量投入、规划和资源的举措，可创造长期价值。",
          },
          {
            heading: "暂缓处理",
            description: "影响较小却需要大量投入的举措，不宜优先处理。",
          },
          {
            heading: "补充事项",
            description: "影响较小但易于实施的事项，可在资源充足时处理。",
          },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"],
      text_color: null,
      decorative: false,
      name: "impact_effort_matrix",
    };
  }

  if (infographicType === "comparison_matrix") {
    const criteria = [
      "市场准入",
      "所需投入",
      "上市速度",
      "控制力",
      "可扩展性",
    ];
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 340 },
      data: {
        type: "comparison_matrix",
        criteria,
        items: [
          { icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.measure, color: "111111" }, heading: "自然增长", values: ["中", "低", "慢", "高", "高"] },
          { icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define, color: "111111" }, heading: "产品创新", values: ["高", "低", "快", "低", "高"] },
          { icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover, color: "111111" }, heading: "战略合作", values: ["低", "低", "慢", "快", "高"] },
          { icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute, color: "111111" }, heading: "并购", values: ["中", "低", "慢", "高", "高"] },
          { icon: { url: INFOGRAPHIC_EXAMPLE_ICON_URLS.plan, color: "111111" }, heading: "市场扩张", values: ["中", "低", "慢", "高", "高"] },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"],
      text_color: null,
      decorative: false,
      name: "comparison_matrix",
    };
  }

  if (infographicType === "org_chart") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 360 },
      data: {
        type: "org_chart",
        items: [
          { id: "ceo", parent_id: null, heading: "总经理", description: "首席执行官" },
          { id: "coo", parent_id: "ceo", heading: "运营总监", description: "首席运营官" },
          { id: "cfo", parent_id: "ceo", heading: "财务总监", description: "首席财务官" },
          { id: "cmo", parent_id: "ceo", heading: "市场总监", description: "首席市场官" },
          { id: "operations", parent_id: "coo", heading: "运营经理", description: "运营经理" },
          { id: "product", parent_id: "cmo", heading: "产品经理", description: "产品经理" },
          { id: "finance", parent_id: "cmo", heading: "财务经理", description: "财务经理" },
          { id: "ops-executive", parent_id: "operations", heading: "运营主管", description: "运营主管" },
          { id: "analyst", parent_id: "operations", heading: "流程分析师", description: "流程分析师" },
          { id: "finance-executive", parent_id: "finance", heading: "财务主管", description: "财务主管" },
          { id: "accountant", parent_id: "finance", heading: "会计", description: "会计" },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"],
      text_color: null,
      decorative: false,
      name: "org_chart",
    };
  }

  if (infographicType === "decision_tree") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 360 },
      data: {
        type: "decision_tree",
        items: [
          { id: "decision", parent_id: null, heading: "市场扩张决策" },
          { id: "demand", parent_id: "decision", heading: "市场需求" },
          { id: "competitive", parent_id: "decision", heading: "竞争地位" },
          { id: "feasibility", parent_id: "decision", heading: "投资可行性" },
          { id: "readiness", parent_id: "decision", heading: "运营准备度" },
          { id: "market-demand", parent_id: "demand", heading: "市场需求" },
          { id: "growth", parent_id: "demand", heading: "增长潜力" },
          { id: "competition", parent_id: "competitive", heading: "竞争程度" },
          { id: "saturation", parent_id: "competitive", heading: "市场饱和度" },
          { id: "capacity", parent_id: "feasibility", heading: "产能有限" },
          { id: "funding", parent_id: "feasibility", heading: "需要资金" },
          { id: "ready", parent_id: "readiness", heading: "准备充分" },
          { id: "partial", parent_id: "readiness", heading: "部分就绪" },
        ],
      },
      colors: ["FFFFFF", "102E79", "24468E", "385EAA", "4D73BE", "6388D0", "D6D6D6"],
      text_color: null,
      decorative: false,
      name: "decision_tree",
    };
  }

  if (infographicType === "mind_map") {
    return {
      type: "infographic",
      position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
      size: { width: 720, height: 380 },
      data: {
        type: "mind_map",
        items: [
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.discover,
              color: "FFFFFF",
            },
            heading: "发现",
            description: "调研问题并识别关键需求。",
            items: [],
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.define,
              color: "FFFFFF",
            },
            heading: "定义",
            description: "明确目标、优先级与方向。",
            items: [],
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.plan,
              color: "FFFFFF",
            },
            heading: "规划",
            description: "制定策略、时间线和行动计划。",
            items: [],
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.execute,
              color: "FFFFFF",
            },
            heading: "执行",
            description: "落实计划并跟踪进度。",
            items: [],
          },
          {
            icon: {
              url: INFOGRAPHIC_EXAMPLE_ICON_URLS.measure,
              color: "FFFFFF",
            },
            heading: "评估",
            description:
              "按关键绩效指标复核结果。",
            items: [],
          },
        ],
      },
      colors: ["FFFFFF", "536AA2", "647DB8", "7894CF", "8AA8E4", "9DC2ED"],
      text_color: "111111",
      decorative: false,
      name: "mind_map",
    };
  }

  return {
    type: "infographic",
    position: { ...DEFAULT_INFOGRAPHIC_INSERT_POSITION },
    size: { width: 320, height: 190 },
    data: {
      type: "gauge",
      min_value: 0,
      max_value: 100,
      value: 76,
    },
    colors: ["E5E7EB", "2563EB"],
    text_color: "111111",
    decorative: false,
    name: "gauge_chart",
  };
}

function createDefaultInfographicInsertElements(kind?: string): SlideElement[] {
  const infographicType = infographicTypeFromPaletteId(kind);
  return infographicType
    ? [fitInfographicElementToData(makeInfographicElement(infographicType))]
    : [];
}

function makeSimpleTableElement(): SlideElement {
  const baseFont: Font = {
    family: "Manrope",
    size: 14,
    color: "#344054",
    line_height: 1.2,
  };
  const headerFont: Font = {
    ...baseFont,
    color: "#101323",
    bold: true,
  };
  const headerFill: Fill = { color: "#F2F4F7", opacity: 1 };
  const bodyFill: Fill = { color: "#FFFFFF", opacity: 1 };

  return {
    type: "table",
    position: { x: 122, y: 128 },
    size: { width: 819, height: 186 },
    columns: [
      makeTableCell({ text: "指标", font: headerFont, color: headerFill }),
      makeTableCell({ text: "当前值", font: headerFont, color: headerFill }),
      makeTableCell({ text: "目标值", font: headerFont, color: headerFill }),
    ],
    rows: [
      [
        makeTableCell({ text: "激活率", font: baseFont, color: bodyFill }),
        makeTableCell({ text: "68%", font: baseFont, color: bodyFill }),
        makeTableCell({ text: "75%", font: baseFont, color: bodyFill }),
      ],
      [
        makeTableCell({ text: "留存率", font: baseFont, color: bodyFill }),
        makeTableCell({ text: "42%", font: baseFont, color: bodyFill }),
        makeTableCell({ text: "50%", font: baseFont, color: bodyFill }),
      ],
    ],
    min_columns: 2,
    max_columns: 6,
    min_rows: 2,
    max_rows: 8,
    decorative: false,
    name: "simple_table",
  };
}

function createDefaultTableInsertElements(kind?: string): SlideElement[] {
  return kind === "simple-table" ? [makeSimpleTableElement()] : [];
}

function makeImageElement({
  x,
  y,
  width,
  height,
  name = "image",
}: {
  x: number;
  y: number;
  width: number;
  height: number;
  name?: string;
}): SlideElement {
  return {
    type: "image",
    position: { x, y },
    size: { width, height },
    data: DEFAULT_IMAGE_PLACEHOLDER_SRC,
    fit: "cover",
    decorative: false,
    name,
    is_icon: false,
    border_radius: IMAGE_RADIUS,
  };
}

function createDefaultImageInsertContent(kind?: string): EditorInsertContent {
  switch (kind) {
    case "image":
      return {
        elements: [
          makeImageElement({
            x: 134,
            y: 128,
            width: 666,
            height: 397,
          }),
        ],
      };
    case "image-text":
      return {
        components: [
          {
            id: "image_text",
            description: "带标题和说明的图片",
            position: { x: 122, y: 128 },
            elements: [
              makeImageElement({ x: 0, y: 0, width: 486, height: 371 }),
              makeTextElement({
                name: "image_heading",
                text: "在此输入标题",
                x: 525,
                y: 15,
                width: 442,
                height: 74,
                size: 24,
                bold: true,
              }),
              makeTextElement({
                name: "image_supporting_text",
                text: "在此输入图片的补充说明。",
                x: 525,
                y: 108,
                width: 442,
                height: 134,
                size: 16,
                color: "475467",
                lineHeight: 1.3,
              }),
            ],
          },
        ],
      };
    case "image-grid":
      return {
        components: [
          {
            id: "image_grid",
            description: "二乘二图片网格",
            position: { x: 128, y: 122 },
            elements: [
              makeImageElement({
                x: 0,
                y: 0,
                width: 346,
                height: 211,
                name: "image_1",
              }),
              makeImageElement({
                x: 371,
                y: 0,
                width: 346,
                height: 211,
                name: "image_2",
              }),
              makeImageElement({
                x: 0,
                y: 243,
                width: 346,
                height: 211,
                name: "image_3",
              }),
              makeImageElement({
                x: 371,
                y: 243,
                width: 346,
                height: 211,
                name: "image_4",
              }),
            ],
          },
        ],
      };
    default:
      return {};
  }
}

const DEFAULT_VECTOR_FILL: Fill = { color: "F4F3FF", opacity: 1 };
const DEFAULT_VECTOR_STROKE: Stroke = { color: "7A5AF8", width: 1.5 };
const DEFAULT_VECTOR_LINE_STROKE: Stroke = {
  ...DEFAULT_VECTOR_STROKE,
  width: 2,
};

export const ELEMENT_INSERT_GROUPS = [
  {
    label: "基本形状",
    items: [
      { id: "vector-rectangle", label: "矩形" },
      { id: "vector-rounded-rectangle", label: "圆角矩形" },
      { id: "vector-capsule", label: "胶囊形" },
      { id: "vector-circle", label: "圆形" },
      { id: "vector-ellipse", label: "椭圆" },
      { id: "vector-triangle", label: "三角形" },
      { id: "vector-right-triangle", label: "直角三角形" },
      { id: "vector-diamond", label: "菱形" },
      { id: "vector-parallelogram", label: "平行四边形" },
      { id: "vector-trapezoid", label: "梯形" },
      { id: "vector-pentagon", label: "五边形" },
      { id: "vector-hexagon", label: "六边形" },
      { id: "vector-octagon", label: "八边形" },
      { id: "vector-teardrop", label: "泪滴形" },
    ],
  },
  {
    label: "线条与箭头",
    items: [
      { id: "vector-line", label: "直线" },
      { id: "vector-line-arrow", label: "单向箭头" },
      { id: "vector-line-arrow-both", label: "双向箭头" },
      { id: "vector-line-stealth", label: "细箭头" },
      { id: "vector-line-filled", label: "实心箭头" },
      { id: "vector-line-filled-both", label: "双向实心箭头" },
      { id: "vector-line-circle-arrow", label: "圆点箭头" },
      { id: "vector-line-square-arrow", label: "方点箭头" },
      { id: "vector-line-diamond-arrow", label: "菱形点箭头" },
    ],
  },
  {
    label: "块状箭头",
    items: [
      { id: "vector-arrow", label: "向右箭头" },
      { id: "vector-arrow-left", label: "向左箭头" },
      { id: "vector-arrow-up", label: "向上箭头" },
      { id: "vector-arrow-down", label: "向下箭头" },
      { id: "vector-arrow-left-right", label: "左右箭头" },
      { id: "vector-arrow-up-down", label: "上下箭头" },
      { id: "vector-chevron-right", label: "折角箭头" },
      { id: "vector-notched-arrow", label: "凹口箭头" },
      { id: "vector-bent-arrow", label: "弯折箭头" },
      { id: "vector-four-way-arrow", label: "四向箭头" },
    ],
  },
  {
    label: "符号",
    items: [
      { id: "vector-plus", label: "加号" },
      { id: "vector-cross", label: "叉号" },
      { id: "vector-lightning", label: "闪电" },
      { id: "vector-home", label: "房屋" },
      { id: "vector-speech-bubble", label: "对话气泡" },
      { id: "vector-cloud", label: "云朵" },
      { id: "vector-heart", label: "爱心" },
      { id: "vector-star", label: "星形" },
      { id: "vector-bookmark", label: "书签" },
      { id: "vector-shield", label: "盾牌" },
      { id: "vector-flag", label: "旗帜" },
      { id: "vector-moon", label: "月亮" },
      { id: "vector-sun", label: "太阳" },
      { id: "vector-play", label: "播放" },
    ],
  },
] as const;

export type ElementInsertKind =
  (typeof ELEMENT_INSERT_GROUPS)[number]["items"][number]["id"];

function makeVector({
  points,
  shape,
  closed = true,
  fill = DEFAULT_VECTOR_FILL,
  stroke = DEFAULT_VECTOR_STROKE,
  curve,
  cornerRadii,
  startMarker,
  endMarker,
}: {
  points: Array<{ x: number; y: number }>;
  shape?: "polygon" | "ellipse";
  closed?: boolean;
  fill?: Fill | null;
  stroke?: Stroke | null;
  curve?: { type: "smooth"; tension?: number; segments?: number };
  cornerRadii?: number[];
  startMarker?:
    | "arrow"
    | "stealth"
    | "triangle"
    | "circle"
    | "square"
    | "diamond";
  endMarker?:
    | "arrow"
    | "stealth"
    | "triangle"
    | "circle"
    | "square"
    | "diamond";
}): SlideElement {
  return {
    type: "vector",
    ...(shape ? { shape } : {}),
    points,
    closed,
    ...(curve ? { curve } : {}),
    ...(cornerRadii ? { corner_radii: cornerRadii } : {}),
    ...(startMarker ? { start_marker: startMarker } : {}),
    ...(endMarker ? { end_marker: endMarker } : {}),
    ...(fill ? { fill } : {}),
    ...(stroke ? { stroke } : {}),
  };
}

function createDefaultElementInsertElements(kind?: string): SlideElement[] {
  switch (kind) {
    case "vector-rectangle":
      return [
        makeVector({
          points: [
            { x: 134, y: 134 },
            { x: 518, y: 134 },
            { x: 518, y: 326 },
            { x: 134, y: 326 },
          ],
        }),
      ];
    case "vector-rounded-rectangle":
      return [
        makeVector({
          points: rectangleVectorPoints(134, 134, 384, 192),
          cornerRadii: [28, 28, 28, 28],
        }),
      ];
    case "vector-capsule":
      return [
        makeVector({
          points: rectangleVectorPoints(134, 158, 440, 180),
          cornerRadii: [90, 90, 90, 90],
        }),
      ];
    case "vector-circle":
      return [
        makeVector({
          shape: "ellipse",
          points: ellipseVectorPoints(134, 134, 220, 220),
        }),
      ];
    case "vector-ellipse":
      return [
        makeVector({
          shape: "ellipse",
          points: ellipseVectorPoints(134, 134, 346, 198),
        }),
      ];
    case "vector-triangle":
      return [
        makeVector({
          points: [
            { x: 326, y: 122 },
            { x: 518, y: 354 },
            { x: 134, y: 354 },
          ],
        }),
      ];
    case "vector-right-triangle":
      return [
        makeVector({
          points: [
            { x: 134, y: 122 },
            { x: 518, y: 354 },
            { x: 134, y: 354 },
          ],
        }),
      ];
    case "vector-diamond":
      return [
        makeVector({
          points: [
            { x: 326, y: 122 },
            { x: 518, y: 238 },
            { x: 326, y: 354 },
            { x: 134, y: 238 },
          ],
        }),
      ];
    case "vector-parallelogram":
      return [
        makeVector({
          points: [
            { x: 210, y: 134 },
            { x: 574, y: 134 },
            { x: 498, y: 326 },
            { x: 134, y: 326 },
          ],
        }),
      ];
    case "vector-trapezoid":
      return [
        makeVector({
          points: [
            { x: 226, y: 134 },
            { x: 482, y: 134 },
            { x: 574, y: 326 },
            { x: 134, y: 326 },
          ],
        }),
      ];
    case "vector-pentagon":
      return [
        makeVector({
          points: regularPolygonVectorPoints(326, 248, 150, 5, -Math.PI / 2),
        }),
      ];
    case "vector-hexagon":
      return [
        makeVector({
          points: regularPolygonVectorPoints(326, 248, 166, 6),
        }),
      ];
    case "vector-octagon":
      return [
        makeVector({
          points: regularPolygonVectorPoints(
            326,
            248,
            160,
            8,
            Math.PI / 8,
          ),
        }),
      ];
    case "vector-teardrop":
      return [
        makeVector({
          points: [
            { x: 326, y: 112 },
            { x: 442, y: 240 },
            { x: 426, y: 330 },
            { x: 326, y: 386 },
            { x: 226, y: 330 },
            { x: 210, y: 240 },
          ],
          curve: { type: "smooth", tension: 0.32, segments: 16 },
        }),
      ];
    case "vector-arrow":
      return [
        makeVector({
          points: rightArrowVectorPoints(),
        }),
      ];
    case "vector-arrow-left":
      return [
        makeVector({
          points: mirrorVectorPoints(rightArrowVectorPoints(), 354),
        }),
      ];
    case "vector-arrow-up":
      return [
        makeVector({
          points: rotateVectorPoints(rightArrowVectorPoints(), 354, 248, -90),
        }),
      ];
    case "vector-arrow-down":
      return [
        makeVector({
          points: rotateVectorPoints(rightArrowVectorPoints(), 354, 248, 90),
        }),
      ];
    case "vector-arrow-left-right":
      return [
        makeVector({
          points: [
            { x: 126, y: 248 },
            { x: 224, y: 158 },
            { x: 224, y: 206 },
            { x: 484, y: 206 },
            { x: 484, y: 158 },
            { x: 582, y: 248 },
            { x: 484, y: 338 },
            { x: 484, y: 290 },
            { x: 224, y: 290 },
            { x: 224, y: 338 },
          ],
        }),
      ];
    case "vector-arrow-up-down":
      return [
        makeVector({
          points: rotateVectorPoints(
            [
              { x: 126, y: 248 },
              { x: 224, y: 158 },
              { x: 224, y: 206 },
              { x: 484, y: 206 },
              { x: 484, y: 158 },
              { x: 582, y: 248 },
              { x: 484, y: 338 },
              { x: 484, y: 290 },
              { x: 224, y: 290 },
              { x: 224, y: 338 },
            ],
            354,
            248,
            90,
          ),
        }),
      ];
    case "vector-chevron-right":
      return [
        makeVector({
          points: [
            { x: 174, y: 142 },
            { x: 320, y: 142 },
            { x: 520, y: 248 },
            { x: 320, y: 354 },
            { x: 174, y: 354 },
            { x: 374, y: 248 },
          ],
        }),
      ];
    case "vector-notched-arrow":
      return [
        makeVector({
          points: [
            { x: 134, y: 158 },
            { x: 422, y: 158 },
            { x: 422, y: 118 },
            { x: 574, y: 248 },
            { x: 422, y: 378 },
            { x: 422, y: 338 },
            { x: 134, y: 338 },
            { x: 214, y: 248 },
          ],
        }),
      ];
    case "vector-bent-arrow":
      return [
        makeVector({
          points: [
            { x: 134, y: 382 },
            { x: 134, y: 220 },
            { x: 402, y: 220 },
            { x: 402, y: 158 },
            { x: 574, y: 278 },
            { x: 402, y: 398 },
            { x: 402, y: 336 },
            { x: 218, y: 336 },
            { x: 218, y: 382 },
          ],
          cornerRadii: [14, 14, 14, 0, 0, 0, 14, 14, 14],
        }),
      ];
    case "vector-four-way-arrow":
      return [
        makeVector({
          points: [
            { x: 326, y: 104 },
            { x: 402, y: 178 },
            { x: 366, y: 178 },
            { x: 366, y: 208 },
            { x: 396, y: 208 },
            { x: 396, y: 172 },
            { x: 472, y: 248 },
            { x: 396, y: 324 },
            { x: 396, y: 288 },
            { x: 366, y: 288 },
            { x: 366, y: 318 },
            { x: 402, y: 318 },
            { x: 326, y: 392 },
            { x: 250, y: 318 },
            { x: 286, y: 318 },
            { x: 286, y: 288 },
            { x: 256, y: 288 },
            { x: 256, y: 324 },
            { x: 180, y: 248 },
            { x: 256, y: 172 },
            { x: 256, y: 208 },
            { x: 286, y: 208 },
            { x: 286, y: 178 },
            { x: 250, y: 178 },
          ],
        }),
      ];
    case "vector-plus":
      return [
        makeVector({
          points: plusVectorPoints(326, 248, 250, 250, 88),
          cornerRadii: Array(12).fill(8),
        }),
      ];
    case "vector-cross":
      return [
        makeVector({
          points: rotateVectorPoints(
            plusVectorPoints(326, 248, 250, 250, 76),
            326,
            248,
            45,
          ),
          cornerRadii: Array(12).fill(6),
        }),
      ];
    case "vector-lightning":
      return [
        makeVector({
          points: [
            { x: 354, y: 104 },
            { x: 216, y: 270 },
            { x: 314, y: 270 },
            { x: 278, y: 400 },
            { x: 454, y: 210 },
            { x: 350, y: 210 },
          ],
        }),
      ];
    case "vector-home":
      return [
        makeVector({
          points: [
            { x: 326, y: 112 },
            { x: 536, y: 254 },
            { x: 480, y: 254 },
            { x: 480, y: 382 },
            { x: 364, y: 382 },
            { x: 364, y: 286 },
            { x: 288, y: 286 },
            { x: 288, y: 382 },
            { x: 172, y: 382 },
            { x: 172, y: 254 },
            { x: 116, y: 254 },
          ],
          cornerRadii: [6, 6, 0, 10, 8, 8, 8, 8, 10, 0, 6],
        }),
      ];
    case "vector-speech-bubble":
      return [
        makeVector({
          points: [
            { x: 142, y: 130 },
            { x: 534, y: 130 },
            { x: 534, y: 318 },
            { x: 326, y: 318 },
            { x: 226, y: 394 },
            { x: 246, y: 318 },
            { x: 142, y: 318 },
          ],
          cornerRadii: [22, 22, 22, 5, 0, 0, 22],
        }),
      ];
    case "vector-cloud":
      return [
        makeVector({
          points: [
            { x: 182, y: 334 },
            { x: 132, y: 286 },
            { x: 158, y: 222 },
            { x: 232, y: 204 },
            { x: 270, y: 142 },
            { x: 360, y: 134 },
            { x: 414, y: 190 },
            { x: 486, y: 190 },
            { x: 532, y: 246 },
            { x: 514, y: 310 },
            { x: 450, y: 338 },
          ],
          curve: { type: "smooth", tension: 0.42, segments: 16 },
        }),
      ];
    case "vector-heart":
      return [
        makeVector({
          points: [
            { x: 326, y: 190 },
            { x: 270, y: 126 },
            { x: 190, y: 126 },
            { x: 142, y: 194 },
            { x: 154, y: 266 },
            { x: 220, y: 330 },
            { x: 326, y: 394 },
            { x: 432, y: 330 },
            { x: 498, y: 266 },
            { x: 510, y: 194 },
            { x: 462, y: 126 },
            { x: 382, y: 126 },
          ],
          curve: { type: "smooth", tension: 0.38, segments: 16 },
        }),
      ];
    case "vector-star":
      return [
        makeVector({
          points: regularStarVectorPoints(326, 248, 158, 72, 5),
          cornerRadii: Array(10).fill(3),
        }),
      ];
    case "vector-bookmark":
      return [
        makeVector({
          points: [
            { x: 230, y: 112 },
            { x: 422, y: 112 },
            { x: 422, y: 394 },
            { x: 326, y: 330 },
            { x: 230, y: 394 },
          ],
          cornerRadii: [12, 12, 4, 0, 4],
        }),
      ];
    case "vector-shield":
      return [
        makeVector({
          points: [
            { x: 170, y: 132 },
            { x: 482, y: 132 },
            { x: 468, y: 284 },
            { x: 410, y: 346 },
            { x: 326, y: 398 },
            { x: 242, y: 346 },
            { x: 184, y: 284 },
          ],
          cornerRadii: [18, 18, 18, 18, 12, 18, 18],
        }),
      ];
    case "vector-flag":
      return [
        makeVector({
          points: [
            { x: 154, y: 126 },
            { x: 510, y: 126 },
            { x: 450, y: 238 },
            { x: 510, y: 350 },
            { x: 154, y: 350 },
          ],
          cornerRadii: [8, 8, 4, 8, 8],
        }),
      ];
    case "vector-moon":
      return [
        makeVector({
          points: [
            { x: 382, y: 112 },
            { x: 268, y: 126 },
            { x: 188, y: 204 },
            { x: 180, y: 292 },
            { x: 250, y: 374 },
            { x: 366, y: 390 },
            { x: 302, y: 330 },
            { x: 278, y: 250 },
            { x: 310, y: 170 },
          ],
          curve: { type: "smooth", tension: 0.34, segments: 16 },
        }),
      ];
    case "vector-sun":
      return [
        makeVector({
          points: regularStarVectorPoints(326, 248, 164, 104, 16),
          cornerRadii: Array(32).fill(3),
        }),
      ];
    case "vector-play":
      return [
        makeVector({
          points: [
            { x: 226, y: 118 },
            { x: 526, y: 248 },
            { x: 226, y: 378 },
          ],
          cornerRadii: [14, 14, 14],
        }),
      ];
    case "vector-line":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
        }),
      ];
    case "vector-line-arrow":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
          endMarker: "arrow",
        }),
      ];
    case "vector-line-arrow-both":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
          startMarker: "arrow",
          endMarker: "arrow",
        }),
      ];
    case "vector-line-stealth":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
          endMarker: "stealth",
        }),
      ];
    case "vector-line-filled":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
          endMarker: "triangle",
        }),
      ];
    case "vector-line-filled-both":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
          startMarker: "triangle",
          endMarker: "triangle",
        }),
      ];
    case "vector-line-circle-arrow":
    case "vector-line-square-arrow":
    case "vector-line-diamond-arrow":
      return [
        makeVector({
          points: horizontalLineVectorPoints(),
          closed: false,
          fill: null,
          stroke: DEFAULT_VECTOR_LINE_STROKE,
          startMarker:
            kind === "vector-line-circle-arrow"
              ? "circle"
              : kind === "vector-line-square-arrow"
                ? "square"
                : "diamond",
          endMarker: "triangle",
        }),
      ];
    default:
      return [];
  }
}

function ellipseVectorPoints(
  x: number,
  y: number,
  width: number,
  height: number,
) {
  const radiusX = width / 2;
  const radiusY = height / 2;
  const centerX = x + radiusX;
  const centerY = y + radiusY;
  return [
    { x: centerX, y },
    { x: x + width, y: centerY },
    { x: centerX, y: y + height },
    { x, y: centerY },
  ];
}

function rectangleVectorPoints(
  x: number,
  y: number,
  width: number,
  height: number,
) {
  return [
    { x, y },
    { x: x + width, y },
    { x: x + width, y: y + height },
    { x, y: y + height },
  ];
}

function rightArrowVectorPoints() {
  return [
    { x: 134, y: 206 },
    { x: 420, y: 206 },
    { x: 420, y: 158 },
    { x: 574, y: 248 },
    { x: 420, y: 338 },
    { x: 420, y: 290 },
    { x: 134, y: 290 },
  ];
}

function horizontalLineVectorPoints() {
  return [
    { x: 204, y: 248 },
    { x: 504, y: 248 },
  ];
}

function mirrorVectorPoints(
  points: Array<{ x: number; y: number }>,
  axisX: number,
) {
  return points.map((point) => ({ x: axisX * 2 - point.x, y: point.y }));
}

function rotateVectorPoints(
  points: Array<{ x: number; y: number }>,
  centerX: number,
  centerY: number,
  degrees: number,
) {
  const radians = (degrees * Math.PI) / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  return points.map((point) => {
    const offsetX = point.x - centerX;
    const offsetY = point.y - centerY;
    return {
      x: centerX + offsetX * cosine - offsetY * sine,
      y: centerY + offsetX * sine + offsetY * cosine,
    };
  });
}

function plusVectorPoints(
  centerX: number,
  centerY: number,
  width: number,
  height: number,
  armWidth: number,
) {
  const left = centerX - width / 2;
  const right = centerX + width / 2;
  const top = centerY - height / 2;
  const bottom = centerY + height / 2;
  const armLeft = centerX - armWidth / 2;
  const armRight = centerX + armWidth / 2;
  const armTop = centerY - armWidth / 2;
  const armBottom = centerY + armWidth / 2;
  return [
    { x: armLeft, y: top },
    { x: armRight, y: top },
    { x: armRight, y: armTop },
    { x: right, y: armTop },
    { x: right, y: armBottom },
    { x: armRight, y: armBottom },
    { x: armRight, y: bottom },
    { x: armLeft, y: bottom },
    { x: armLeft, y: armBottom },
    { x: left, y: armBottom },
    { x: left, y: armTop },
    { x: armLeft, y: armTop },
  ];
}

function regularStarVectorPoints(
  centerX: number,
  centerY: number,
  outerRadius: number,
  innerRadius: number,
  tips: number,
  rotation = -Math.PI / 2,
) {
  return Array.from({ length: tips * 2 }, (_, index) => {
    const radius = index % 2 === 0 ? outerRadius : innerRadius;
    const angle = rotation + (Math.PI * index) / tips;
    return {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    };
  });
}

function regularPolygonVectorPoints(
  centerX: number,
  centerY: number,
  radius: number,
  sides: number,
  rotation = 0,
) {
  return Array.from({ length: sides }, (_, index) => {
    const angle = rotation + (Math.PI * 2 * index) / sides;
    return {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    };
  });
}

function themeFont(
  font: Font | null | undefined,
  color: string,
  family?: string,
): Font {
  return { ...(font ?? {}), color, ...(family ? { family } : {}) };
}

function themeRuns<T extends { font?: Font | null }>(
  runs: T[],
  color: string,
  family?: string,
) {
  return runs.map((run) => ({
    ...run,
    ...(run.font ? { font: themeFont(run.font, color, family) } : {}),
  }));
}

function themeTableCell(
  cell: TableCell,
  theme: TemplateTheme,
  isHeader: boolean,
): TableCell {
  const family = theme.fonts?.textFont?.name;
  return {
    ...cell,
    color: {
      ...(cell.color ?? {}),
      color: isHeader ? theme.primary : theme.background,
    },
    font: themeFont(
      cell.font,
      isHeader ? theme.primary_text : theme.background_text,
      family,
    ),
    runs: themeRuns(
      cell.runs,
      isHeader ? theme.primary_text : theme.background_text,
      family,
    ),
  };
}

function themeInfographicIcons(value: unknown, color: string): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => themeInfographicIcons(item, color));
  }
  if (!value || typeof value !== "object") return value;

  const record = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.entries(record).map(([key, entry]) => {
      if (key === "icon" && entry && typeof entry === "object") {
        return [key, { ...(entry as Record<string, unknown>), color }];
      }
      return [key, themeInfographicIcons(entry, color)];
    }),
  );
}

function applyTemplateThemeToElement(
  element: SlideElement,
  theme: TemplateTheme,
): SlideElement {
  const graphColors = templateThemeGraphColors(theme);
  const family = theme.fonts?.textFont?.name;

  switch (element.type) {
    case "text":
      return {
        ...element,
        font: themeFont(element.font, theme.background_text, family),
        runs: themeRuns(element.runs, theme.background_text, family),
      };
    case "text-list":
      return {
        ...element,
        font: themeFont(element.font, theme.background_text, family),
        items: element.items.map((item) =>
          themeRuns(item, theme.background_text, family),
        ),
      };
    case "table":
      return {
        ...element,
        font: themeFont(element.font, theme.background_text, family),
        columns: element.columns.map((cell) =>
          themeTableCell(cell, theme, true),
        ),
        rows: element.rows.map((row) =>
          row.map((cell) => themeTableCell(cell, theme, false)),
        ),
      };
    case "chart":
      return {
        ...element,
        color: graphColors[0],
        colors: graphColors,
        axis_color: theme.stroke,
        grid_color: theme.stroke,
        title_color: theme.background_text,
        legend_color: theme.background_text,
        data: element.data.map((datum, index) => ({
          ...datum,
          color: graphColors[index % graphColors.length],
        })),
      };
    case "infographic": {
      const meter =
        element.data.type === "progress_bar" || element.data.type === "gauge";
      const themedData = themeInfographicIcons(
        element.data,
        theme.primary_text,
      ) as typeof element.data;
      const data = {
        ...themedData,
        card_color: theme.card,
        background_text_color: theme.background_text,
      };
      return {
        ...element,
        data:
          data.type === "customer_journey"
            ? { ...data, start_color: graphColors[0] }
            : data,
        colors: meter
          ? [theme.card, theme.primary]
          : [theme.background, ...graphColors, theme.card, theme.stroke],
        text_color: theme.background_text,
      };
    }
    case "vector":
      return {
        ...element,
        ...(element.fill
          ? { fill: { ...element.fill, color: theme.primary } }
          : {}),
        ...(element.stroke
          ? { stroke: { ...element.stroke, color: theme.stroke } }
          : {}),
      };
    case "image":
      return element.is_icon ? { ...element, color: theme.primary } : element;
    case "container":
      return {
        ...element,
        ...(element.fill
          ? { fill: { ...element.fill, color: theme.card } }
          : {}),
        ...(element.stroke
          ? { stroke: { ...element.stroke, color: theme.stroke } }
          : {}),
        child: element.child
          ? applyTemplateThemeToElement(element.child, theme)
          : element.child,
      };
    case "flex":
    case "grid":
    case "group":
      return {
        ...element,
        children: element.children.map((child) =>
          applyTemplateThemeToElement(child, theme),
        ),
      };
    default:
      return element;
  }
}

function themeElements(elements: SlideElement[], theme: TemplateTheme) {
  return elements.map((element) => applyTemplateThemeToElement(element, theme));
}

export function createTextInsertElements(
  kind?: string,
  theme: TemplateTheme = DEFAULT_TEMPLATE_THEME,
): SlideElement[] {
  return themeElements(createDefaultTextInsertElements(kind), theme);
}

export function createChartInsertElements(
  kind?: string,
  theme: TemplateTheme = DEFAULT_TEMPLATE_THEME,
): SlideElement[] {
  return themeElements(createDefaultChartInsertElements(kind), theme);
}

export function createInfographicInsertElements(
  kind?: string,
  theme: TemplateTheme = DEFAULT_TEMPLATE_THEME,
): SlideElement[] {
  return themeElements(createDefaultInfographicInsertElements(kind), theme);
}

export function createTableInsertElements(
  kind?: string,
  theme: TemplateTheme = DEFAULT_TEMPLATE_THEME,
): SlideElement[] {
  return themeElements(createDefaultTableInsertElements(kind), theme);
}

export function createImageInsertContent(
  kind?: string,
  theme: TemplateTheme = DEFAULT_TEMPLATE_THEME,
): EditorInsertContent {
  const content = createDefaultImageInsertContent(kind);
  return {
    ...content,
    elements: content.elements
      ? themeElements(content.elements, theme)
      : content.elements,
    components: content.components?.map((component) => ({
      ...component,
      elements: themeElements(component.elements, theme),
    })),
  };
}

export function createElementInsertElements(
  kind?: string,
  theme: TemplateTheme = DEFAULT_TEMPLATE_THEME,
): SlideElement[] {
  return themeElements(createDefaultElementInsertElements(kind), theme);
}
