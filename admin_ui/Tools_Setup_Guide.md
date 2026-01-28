# HTTP Tools Setup Guide (Admin UI)

**Version**: 1.0.0  
**Last Updated**: January 2026  
**Applies to**: v4.2.0+

---

## Overview

This guide explains how to set up **Pre-Call HTTP Lookups** and **Post-Call Webhooks** using the Admin UI. These tools enable CRM integration with platforms like GoHighLevel, n8n, and Make.

### What You'll Learn

- Setting up pre-call CRM lookups (fetch customer data before AI speaks)
- Configuring post-call webhooks (send call data to external systems)
- Using variables in prompts and payloads
- Integration examples for GoHighLevel, n8n, and Make

---

## Prerequisites

1. Admin UI running and accessible at `http://localhost:3003`
2. API keys for your integration platform (GoHighLevel, n8n, Make)
3. Webhook URLs from your automation platform

---

## Part 1: Pre-Call HTTP Lookups

Pre-call lookups fetch data from external APIs (like CRMs) **after the call is answered but before the AI speaks**. This allows the AI to greet callers by name and provide personalized service.

### Step 1: Navigate to Tools

1. Log in to Admin UI
2. Go to **Tools**
3. Select the **Pre-Call** tab
4. Click **+ Add Pre-Call Tool**

### Step 2: Configure the Lookup

Fill in the following fields:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | Unique identifier for this tool | `ghl_contact_lookup` |
| **Enabled** | Toggle to enable/disable | ✓ |
| **Global** | Run for all contexts (can be opted out per context) | ☐ |
| **URL** | API endpoint URL | `https://api.example.com/contacts/lookup` |
| **Method** | HTTP method | `GET` |
| **Timeout (ms)** | Request timeout | `2000` |

### Step 3: Add Headers

Click **Add Header** and configure authentication:

| Header Name | Value |
|-------------|-------|
| `Authorization` | `Bearer ${GHL_API_KEY}` |

> **Note**: Use `${ENV_VAR}` syntax to reference environment variables securely.

### Step 4: Add Query Parameters

Click **Add Query Param** to pass caller information:

| Parameter | Value |
|-----------|-------|
| `phone` | `{caller_number}` |

### Step 5: Configure Output Variables

Output variables map API response fields to prompt variables:

| Variable Name | Response Path | Description |
|---------------|---------------|-------------|
| `customer_name` | `contacts[0].firstName` | Customer's first name |
| `customer_email` | `contacts[0].email` | Customer's email |
| `customer_company` | `contacts[0].companyName` | Company name |

**Path Syntax**:
- Simple field: `firstName`
- Nested field: `contact.email`
- Array element: `contacts[0].name`

> **MVP note**: Output mappings support **simple dot paths + `[index]`** only. Expressions (concatenation, filters, joins) are Post-MVP.

### Step 6: Save

Click **Save** to apply changes.

> **Test Connection** is **Post-MVP** (not required for MVP).

---

## Part 2: Post-Call Webhooks

Post-call webhooks send call data to external systems **after the call ends**. Use them to update CRMs, trigger automations, or log calls.

### Step 1: Navigate to Tools

1. Go to **Tools**
2. Select the **Post-Call** tab
3. Click **+ Add Post-Call Tool**

### Step 2: Configure the Webhook

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | Unique identifier | `n8n_call_completed` |
| **Enabled** | Toggle on/off | ✓ |
| **Global** | Run for ALL calls | ✓ |
| **URL** | Webhook endpoint | `https://n8n.example.com/webhook/calls` |
| **Method** | HTTP method | `POST` |
| **Timeout (ms)** | Request timeout | `10000` |

### Step 3: Add Headers

| Header Name | Value |
|-------------|-------|
| `Content-Type` | `application/json` |
| `Authorization` | `Bearer ${WEBHOOK_TOKEN}` |

### Step 4: Configure Payload Template

Enter your JSON payload template in the **Payload Template** field:

```json
{
  "call_id": "{call_id}",
  "caller_phone": "{caller_number}",
  "caller_name": "{caller_name}",
  "duration_seconds": {call_duration},
  "outcome": "{call_outcome}",
  "summary": "{summary}",
  "summary_json": {summary_json},
  "transcript": {transcript_json},
  "pre_call_results": {pre_call_results_json},
  "tool_calls": {tool_calls_json}
}
```

### Step 5: Enable AI Summary (Optional)

Toggle **Generate Summary** to have the AI create a summary of the conversation:

| Field | Description |
|-------|-------------|
| **Generate Summary** | ✓ Enable |
| **Max Words** | `100` |

When enabled, `{summary}` contains an AI-generated summary and `{summary_json}` contains the same summary as a JSON string (safe for unquoted insertion).

### Step 6: Save

Click **Save Configuration** to apply changes.

---

## Part 3: Using Variables in Contexts

After setting up lookups and webhooks, you need to use the output variables in your AI context prompts.

### Step 1: Navigate to Contexts

1. Go to **Contexts**
2. Select an existing context or click **Add Context**

### Step 2: Use Pre-Call Variables in Prompts

In the **System Prompt** field, reference your lookup output variables:

```
You are a helpful customer support agent.

Customer Information:
- Name: {customer_name}
- Company: {customer_company}
- Email: {customer_email}

Greet the customer by name and provide personalized assistance.
If the customer name is empty, ask for their name politely.
```

### Step 3: Enable Tools for the Context

In the **Tools Configuration** section, enable tools per phase:

- **Pre-Call Tools**: select lookups to run after answer and before the AI speaks (`pre_call_tools`).
- **In-Call Tools**: select callable tools for the live conversation (`tools`).
- **Post-Call Tools**: select webhooks/actions to run after hangup (`post_call_tools`).

> **Global tools** run for all contexts by default. Per context you can opt out via:
> - `disable_global_pre_call_tools`
> - `disable_global_post_call_tools`

### Step 4: Save Context

Click **Save** to apply the context configuration.

---

## Part 4: Variable Reference

### Pre-Call Variables (Input)

Use these in lookup URLs, query params, and headers:

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `{caller_number}` | Caller's phone number | `+15551234567` |
| `{called_number}` | DID or extension that was dialed | `+18005551234` or `3000` |
| `{caller_name}` | Caller ID name | `John Smith` |
| `{caller_id}` | Alias for `{caller_number}` (best for prompt/greeting templates) | `+15551234567` |
| `{call_id}` | Unique call identifier | `1763582071.6214` |
| `{context_name}` | AI context name | `support` |
| `{campaign_id}` | Outbound campaign ID | `camp_abc123` |
| `{lead_id}` | Outbound lead ID | `lead_xyz789` |
| `${ENV_VAR}` | Environment variable | (from .env file) |

> **Note**: `{caller_id}` is primarily intended for **prompt/greeting templates**. For lookup URL/query/body templates, use `{caller_number}`.

> **Called Number**: `{called_number}` is automatically captured from `__FROM_DID` (external calls) or `DIALED_NUMBER` dialplan variable (internal calls). For internal extensions, set `DIALED_NUMBER` in dialplan before Stasis (e.g., `Set(DIALED_NUMBER=3000)`).

### Post-Call Variables (Output)

Use these in webhook payloads:

| Variable | Type | Description |
|----------|------|-------------|
| `{call_id}` | string | Unique call identifier |
| `{caller_number}` | string | Caller's phone number |
| `{called_number}` | string | DID that was called |
| `{caller_name}` | string | Caller ID name |
| `{context_name}` | string | AI context used |
| `{provider}` | string | AI provider name |
| `{call_direction}` | string | `inbound` or `outbound` |
| `{call_duration}` | number | Duration in seconds |
| `{call_outcome}` | string | Call outcome |
| `{call_start_time}` | string | ISO timestamp |
| `{call_end_time}` | string | ISO timestamp |
| `{summary}` | string | AI-generated summary (if enabled) |
| `{summary_json}` | JSON | AI-generated summary as a JSON string (safe for unquoted insertion) |
| `{transcript_json}` | JSON | Full conversation array |
| `{pre_call_results_json}` | JSON | Pre-call tool outputs (key/value JSON) |
| `{tool_calls_json}` | JSON | In-call tool call log (JSON) |
| `{campaign_id}` | string | Campaign ID |
| `{lead_id}` | string | Lead ID |

> **Important**: `{transcript_json}` inserts raw JSON (not quoted). Place it directly without quotes.

---

## Part 5: Platform-Specific Setup

### GoHighLevel Integration

GoHighLevel API endpoints and authentication details can change; validate request/response shape against GoHighLevel’s official docs.

- MVP pattern: run a **pre-call lookup** to fetch the contact and store a `contact_id` in pre-call results, then send a **post-call webhook** including `{pre_call_results_json}` and `{summary_json}` so your automation can update the contact.

---

### n8n Integration

**Post-Call: Trigger Workflow**

1. In n8n, create a workflow with **Webhook** trigger node
2. Copy the webhook URL
3. In Admin UI, create a webhook:

| Field | Value |
|-------|-------|
| **Name** | `n8n_call_completed` |
| **URL** | `https://your-n8n.com/webhook/xxxxx` |
| **Method** | `POST` |
| **Global** | ✓ |
| **Generate Summary** | ✓ |

4. **Payload**:
```json
{
  "event": "call_completed",
  "call_id": "{call_id}",
  "caller": {
    "phone": "{caller_number}",
    "name": "{caller_name}"
  },
  "duration": {call_duration},
  "outcome": "{call_outcome}",
  "summary_json": {summary_json},
  "transcript": {transcript_json},
  "timestamp": "{call_end_time}"
}
```

**n8n Workflow Example**:
```
[Webhook] → [IF: Check Outcome]
              ├─ "transferred" → [Slack: Notify Team]
              ├─ "completed" → [Google Sheets: Log Call]
              └─ default → [Email: Send Summary]
```

---

### Make (Integromat) Integration

**Post-Call: Trigger Scenario**

1. In Make, create a scenario with **Webhooks > Custom webhook** module
2. Click "Add" to create webhook and copy URL
3. In Admin UI, create a webhook:

| Field | Value |
|-------|-------|
| **Name** | `make_call_completed` |
| **URL** | `https://hook.us1.make.com/xxxxx` |
| **Method** | `POST` |
| **Global** | ✓ |
| **Generate Summary** | ✓ |

4. **Payload**:
```json
{
  "call_id": "{call_id}",
  "caller_phone": "{caller_number}",
  "duration_seconds": {call_duration},
  "outcome": "{call_outcome}",
  "ai_summary_json": {summary_json},
  "transcript": {transcript_json}
}
```

**Make Scenario Example**:
```
[Webhook] → [Router]
              ├─ Filter: outcome = "completed" → [HubSpot: Create Note]
              └─ Filter: outcome = "transferred" → [Slack: Send Message]
```

---

## Troubleshooting

### Lookup Returns Empty Values

1. **Check URL**: Verify the API endpoint is correct
2. **Check Headers**: Ensure API key is set in `.env`
3. **Check Response Path**: Use browser dev tools to inspect actual API response
4. **Check Timeout**: Increase timeout if API is slow

### Webhook Not Triggering

1. **Check Enabled**: Ensure webhook is enabled
2. **Check Global**: If not global, ensure it's enabled in the context
3. **Check Logs**: View ai_engine logs for errors:
   ```bash
   docker logs ai_engine | grep webhook
   ```

### Variables Not Substituting

1. **Syntax**: Use `{variable}` for call variables, `${VAR}` for env variables
2. **Spelling**: Variable names are case-sensitive
3. **JSON Fields**: Don't quote `{transcript_json}` - it's inserted as raw JSON

### API Key Not Found

1. Add the key to `.env` file:
   ```
   GHL_API_KEY=your_key_here
   ```
2. Restart containers:
   ```bash
   docker compose restart ai_engine
   ```

---

## Best Practices

1. **Use Environment Variables** for API keys - never hardcode secrets
2. **Set Appropriate Timeouts** - pre-call lookups should be fast (2-3s max)
3. **Enable Summaries** for webhooks - AI summaries are more useful than raw transcripts
4. **Test with Low Traffic** first before enabling globally
5. **Monitor Logs** for failed requests during initial setup
6. **Use Global Webhooks** for logging/analytics that should run on every call

---

## Related Documentation

- [Admin UI Setup Guide](UI_Setup_Guide.md) - General UI setup
- [Tool Calling Guide](../docs/TOOL_CALLING_GUIDE.md) - Complete tool reference
- [Configuration Reference](../docs/Configuration-Reference.md) - All YAML settings
