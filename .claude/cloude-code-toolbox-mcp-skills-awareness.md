# Cloude Code ToolBox — MCP & Skills awareness

_Generated: 2026-06-30T09:42:53.261Z_

## How to use this report

- **Saved copy:** This file is **`.claude/cloude-code-toolbox-mcp-skills-awareness.md`** — refreshed whenever the toolbox runs an MCP & Skills scan (including on workspace open when auto-scan is enabled). It is meant for **Claude Code workspace context** together with `CLAUDE.md` (which gets a shorter replaceable summary when auto-merge is on).
- **MCP:** Lists **configured** servers from Claude Code config (`~/.claude.json` for user scope, `.mcp.json` for project scope). Use `/mcp` in the Claude Code panel to connect servers for your session.
- **Skills:** **On-disk** folders with `SKILL.md`. Claude Code does not auto-load them; attach `SKILL.md` or paths in chat when useful.
- **Task routing:** When the user’s request matches a server’s purpose (e.g. Confluence → Confluence/Atlassian MCP), prefer that **server id** from the tables below.

---

## MCP — workspace

Workspace `mcp.json` _(folder: OSkhQuant)_

- **d:\02Project\OSkhQuant\.mcp.json** — _File missing_

_No active workspace servers in mcp.json._

## MCP — user profile

- **C:\Users\pp313\.claude.json** — _File exists — no servers defined_

_No active user-scoped servers in mcp.json._

## Skills (local `SKILL.md` folders)

### Project-scoped

_None found (or no workspace open)._

### User-scoped

- **a-stock-data** — `C:\Users\pp313\.claude\skills\a-stock-data`
  - A股全栈数据工具包 — 覆盖行情(mootdx+腾讯+百度K线)、研报(东财+同花顺+iwencai)、信号(同花顺热点+北向+龙虎榜+解禁+行业)、资金面(融资融券+大宗交易+股东户数+分红+资金流分钟级+资金流120日)、新闻(东财个股+全球资讯)、基础数据(mootdx财务/F10+东财+新浪三表)、公告(巨潮)七层数据源，内嵌全部调用代码，自包含零依赖外部文件。优先用通达信(mootdx)/腾讯(不封IP)，东财接口已内置限流防

- **A15-export** — `C:\Users\pp313\.claude\skills\A15-export`
  - >

- **autosar-bsw-expert** — `C:\Users\pp313\.claude\skills\autosar-bsw-expert`
  - >

- **CCU-export** — `C:\Users\pp313\.claude\skills\CCU-export`
  - >

- **diag-expert** — `C:\Users\pp313\.claude\skills\diag-expert`
  - >

- **serenity-skill** — `C:\Users\pp313\.claude\skills\serenity-skill`
  - Turn an investment agent into a supply-chain bottleneck hunter. Use this skill for source-backed investment research, live market/theme scans, AI/semi/technology value-chain mapping, A-share/HK/US stock screening, thesis

- **tricore-expert** — `C:\Users\pp313\.claude\skills\tricore-expert`
  - >

---

## Suggested next steps

- **MCP:** Use this extension’s hub **MCP** tab, or `claude mcp list` in the terminal. In Claude Code, use `/mcp` to connect servers for the session.
- **Edit config:** Open `~/.claude.json` (user MCP) or `<workspace>/.mcp.json` (project MCP) via the extension commands.
- **Refresh this report:** run **Intelligence — scan MCP & Skills awareness** again after changing MCP config or adding skills.

_Report from Cloude Code ToolBox extension._
