package troubleshoot

import (
	"context"
	"fmt"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/fatih/color"
)

var (
	successColor = color.New(color.FgGreen)
	errorColor   = color.New(color.FgRed)
	warningColor = color.New(color.FgYellow)
	infoColor    = color.New(color.FgBlue)
)

// Call represents a call record
type Call struct {
	ID        string
	Timestamp time.Time
	Duration  string
	Status    string
	Channel   string
}

// Runner orchestrates troubleshooting
type Runner struct {
	verbose     bool
	ctx         context.Context
	callID      string
	symptom     string
	interactive bool
	collectOnly bool
	noLLM       bool
	list        bool
}

// NewRunner creates a new troubleshoot runner
func NewRunner(callID, symptom string, interactive, collectOnly, noLLM, list, verbose bool) *Runner {
	return &Runner{
		verbose:     verbose,
		ctx:         context.Background(),
		callID:      callID,
		symptom:     symptom,
		interactive: interactive,
		collectOnly: collectOnly,
		noLLM:       noLLM,
		list:        list,
	}
}

// Run executes troubleshooting workflow
func (r *Runner) Run() error {
	// Load .env file for API keys
	LoadEnvFile()
	
	fmt.Println()
	fmt.Println("🔍 Call Troubleshooting & RCA")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()

	// List mode
	if r.list {
		return r.listCalls()
	}

	// Determine which call to analyze
	if r.callID == "" || r.callID == "last" {
		calls, err := r.getRecentCalls(10)
		if err != nil {
			return fmt.Errorf("failed to get recent calls: %w", err)
		}
		if len(calls) == 0 {
			errorColor.Println("❌ No recent calls found")
			fmt.Println()
			fmt.Println("Tips:")
			fmt.Println("  • Make a test call first")
			fmt.Println("  • Check if ai_engine container is running")
			fmt.Println("  • Verify logs: docker logs ai_engine")
			return fmt.Errorf("no calls to analyze")
		}
		
		// If --last flag or "last", use most recent
		if r.callID == "last" {
			r.callID = calls[0].ID
			infoColor.Printf("Analyzing most recent call: %s\n", r.callID)
			fmt.Println()
		} else {
			// No call ID and no --last flag: interactive selection
			selectedID, err := SelectCallInteractive(calls)
			if err != nil {
				return err
			}
			r.callID = selectedID
			infoColor.Printf("Analyzing call: %s\n", r.callID)
			fmt.Println()
		}
	}

	// Collect logs and data
	infoColor.Println("Collecting call data...")
	logData, err := r.collectCallData()
	if err != nil {
		return fmt.Errorf("failed to collect data: %w", err)
	}
	successColor.Println("✅ Data collected")
	fmt.Println()

	if r.collectOnly {
		fmt.Println("Data collection complete. Files saved to logs/")
		return nil
	}

	// Analyze logs
	infoColor.Println("Analyzing logs...")
	analysis := r.analyzeBasic(logData)
	
	// Apply symptom-specific analysis
	if r.symptom != "" {
		infoColor.Printf("Applying symptom analysis: %s\n", r.symptom)
		checker := NewSymptomChecker(r.symptom)
		checker.AnalyzeSymptom(analysis, logData)
	}
	
	// LLM analysis
	var llmDiagnosis *LLMDiagnosis
	if !r.noLLM {
		infoColor.Println("Requesting AI diagnosis...")
		llmAnalyzer, err := NewLLMAnalyzer()
		if err != nil {
			warningColor.Printf("⚠️  LLM analysis unavailable: %v\n", err)
		} else {
			llmDiagnosis, err = llmAnalyzer.AnalyzeWithLLM(analysis, logData)
			if err != nil {
				warningColor.Printf("⚠️  LLM analysis failed: %v\n", err)
			} else {
				successColor.Println("✅ AI diagnosis complete")
			}
		}
	}
	fmt.Println()

	// Show findings
	r.displayFindings(analysis)
	
	// Show LLM diagnosis
	if llmDiagnosis != nil {
		r.displayLLMDiagnosis(llmDiagnosis)
	}

	// Interactive follow-up
	if r.interactive {
		return r.interactiveSession(analysis)
	}

	return nil
}

// listCalls lists recent calls
func (r *Runner) listCalls() error {
	calls, err := r.getRecentCalls(20)
	if err != nil {
		return err
	}

	if len(calls) == 0 {
		warningColor.Println("No recent calls found")
		return nil
	}

	fmt.Printf("Recent calls (%d):\n\n", len(calls))
	for i, call := range calls {
		age := time.Since(call.Timestamp)
		ageStr := formatDuration(age)
		fmt.Printf("%2d. %s - %s ago", i+1, call.ID, ageStr)
		if call.Duration != "" {
			fmt.Printf(" (duration: %s)", call.Duration)
		}
		fmt.Println()
	}
	fmt.Println()
	fmt.Println("Usage: agent troubleshoot --call <id>")
	return nil
}

// getRecentCalls extracts recent calls from logs
func (r *Runner) getRecentCalls(limit int) ([]Call, error) {
	cmd := exec.Command("docker", "logs", "--since", "24h", "ai_engine")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("failed to read logs: %w", err)
	}

	callMap := make(map[string]*Call)
	
	// Multiple patterns to catch different log formats
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`"call_id":\s*"([0-9]+\.[0-9]+)"`),           // JSON: "call_id": "1761518880.2191"
		regexp.MustCompile(`call_id[=:][\s]*"?([0-9]+\.[0-9]+)"?`),      // call_id=1761518880.2191 or call_id: "..."
		regexp.MustCompile(`channel_id"?:\s*"?([0-9]+\.[0-9]+)"?`),      // channel_id: "1761518880.2191"
		regexp.MustCompile(`\b([0-9]{10}\.[0-9]{4})\b`),                 // Plain number pattern
	}
	
	lines := strings.Split(string(output), "\n")
	for _, line := range lines {
		for _, pattern := range patterns {
			matches := pattern.FindStringSubmatch(line)
			if len(matches) > 1 {
				callID := matches[1]
				if _, exists := callMap[callID]; !exists {
					callMap[callID] = &Call{
						ID:        callID,
						Timestamp: time.Now(), // Will be refined from log timestamp
					}
				}
				break // Found a match, no need to try other patterns
			}
		}
	}

	// Convert to slice and sort by ID (descending, newer first)
	calls := make([]Call, 0, len(callMap))
	for _, call := range callMap {
		calls = append(calls, *call)
	}
	
	sort.Slice(calls, func(i, j int) bool {
		return calls[i].ID > calls[j].ID
	})

	if len(calls) > limit {
		calls = calls[:limit]
	}

	return calls, nil
}

// collectCallData collects logs for specific call
func (r *Runner) collectCallData() (string, error) {
	cmd := exec.Command("docker", "logs", "--since", "1h", "ai_engine")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", err
	}

	// Filter logs for this call ID
	allLogs := string(output)
	lines := strings.Split(allLogs, "\n")
	var callLogs []string
	
	for _, line := range lines {
		if strings.Contains(line, r.callID) {
			callLogs = append(callLogs, line)
		}
	}

	return strings.Join(callLogs, "\n"), nil
}

// Analysis holds analysis results
type Analysis struct {
	CallID            string
	Errors            []string
	Warnings          []string
	AudioIssues       []string
	Metrics           map[string]string
	HasAudioSocket    bool
	HasTranscription  bool
	HasPlayback       bool
	Symptom           string
	SymptomAnalysis   *SymptomAnalysis
}

// analyzeBasic performs basic log analysis
func (r *Runner) analyzeBasic(logData string) *Analysis {
	analysis := &Analysis{
		CallID:  r.callID,
		Metrics: make(map[string]string),
		Symptom: r.symptom,
	}

	lines := strings.Split(logData, "\n")
	
	for _, line := range lines {
		lower := strings.ToLower(line)
		
		// Check for errors
		if strings.Contains(lower, "error") && !strings.Contains(lower, "0 error") {
			analysis.Errors = append(analysis.Errors, line)
		}
		
		// Check for warnings
		if strings.Contains(lower, "warning") || strings.Contains(lower, "warn") {
			analysis.Warnings = append(analysis.Warnings, line)
		}
		
		// Audio pipeline indicators
		if strings.Contains(lower, "audiosocket") {
			analysis.HasAudioSocket = true
		}
		if strings.Contains(lower, "transcription") || strings.Contains(lower, "transcript") {
			analysis.HasTranscription = true
		}
		if strings.Contains(lower, "playback") || strings.Contains(lower, "playing") {
			analysis.HasPlayback = true
		}
		
		// Audio quality issues
		if strings.Contains(lower, "underflow") {
			analysis.AudioIssues = append(analysis.AudioIssues, "Jitter buffer underflow detected")
		}
		if strings.Contains(lower, "garbled") || strings.Contains(lower, "distorted") {
			analysis.AudioIssues = append(analysis.AudioIssues, "Audio quality issue detected")
		}
		if strings.Contains(lower, "echo") {
			analysis.AudioIssues = append(analysis.AudioIssues, "Echo detected")
		}
	}

	return analysis
}

// displayFindings shows analysis results
func (r *Runner) displayFindings(analysis *Analysis) {
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println("📊 ANALYSIS RESULTS")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()

	// Pipeline status
	fmt.Println("Pipeline Status:")
	if analysis.HasAudioSocket {
		successColor.Println("  ✅ AudioSocket: Active")
	} else {
		errorColor.Println("  ❌ AudioSocket: Not detected")
	}
	
	if analysis.HasTranscription {
		successColor.Println("  ✅ Transcription: Active")
	} else {
		warningColor.Println("  ⚠️  Transcription: Not detected")
	}
	
	if analysis.HasPlayback {
		successColor.Println("  ✅ Playback: Active")
	} else {
		warningColor.Println("  ⚠️  Playback: Not detected")
	}
	fmt.Println()

	// Audio issues
	if len(analysis.AudioIssues) > 0 {
		errorColor.Printf("Audio Issues Found (%d):\n", len(analysis.AudioIssues))
		for _, issue := range analysis.AudioIssues {
			fmt.Printf("  • %s\n", issue)
		}
		fmt.Println()
	}

	// Errors
	if len(analysis.Errors) > 0 {
		errorColor.Printf("Errors (%d):\n", len(analysis.Errors))
		count := len(analysis.Errors)
		if count > 5 {
			count = 5
		}
		for i := 0; i < count; i++ {
			fmt.Printf("  %d. %s\n", i+1, truncate(analysis.Errors[i], 100))
		}
		if len(analysis.Errors) > 5 {
			fmt.Printf("  ... and %d more\n", len(analysis.Errors)-5)
		}
		fmt.Println()
	}

	// Warnings
	if len(analysis.Warnings) > 0 {
		warningColor.Printf("Warnings (%d):\n", len(analysis.Warnings))
		count := len(analysis.Warnings)
		if count > 3 {
			count = 3
		}
		for i := 0; i < count; i++ {
			fmt.Printf("  %d. %s\n", i+1, truncate(analysis.Warnings[i], 100))
		}
		if len(analysis.Warnings) > 3 {
			fmt.Printf("  ... and %d more\n", len(analysis.Warnings)-3)
		}
		fmt.Println()
	}

	// Symptom-specific analysis
	if analysis.SymptomAnalysis != nil {
		fmt.Println("═══════════════════════════════════════════")
		warningColor.Printf("SYMPTOM ANALYSIS: %s\n", analysis.SymptomAnalysis.Symptom)
		fmt.Println("═══════════════════════════════════════════")
		fmt.Printf("%s\n\n", analysis.SymptomAnalysis.Description)
		
		if len(analysis.SymptomAnalysis.Findings) > 0 {
			fmt.Println("Findings:")
			for _, finding := range analysis.SymptomAnalysis.Findings {
				fmt.Printf("  %s\n", finding)
			}
			fmt.Println()
		}
		
		if len(analysis.SymptomAnalysis.RootCauses) > 0 {
			errorColor.Println("Likely Root Causes:")
			for _, cause := range analysis.SymptomAnalysis.RootCauses {
				fmt.Printf("  • %s\n", cause)
			}
			fmt.Println()
		}
		
		if len(analysis.SymptomAnalysis.Actions) > 0 {
			successColor.Println("Recommended Actions:")
			for i, action := range analysis.SymptomAnalysis.Actions {
				fmt.Printf("  %d. %s\n", i+1, action)
			}
			fmt.Println()
		}
	}

	// Basic recommendations
	r.displayRecommendations(analysis)
}

// displayRecommendations shows basic recommendations
func (r *Runner) displayRecommendations(analysis *Analysis) {
	fmt.Println("Recommendations:")
	
	if !analysis.HasAudioSocket {
		fmt.Println("  • Check if AudioSocket is configured correctly")
		fmt.Println("  • Verify port 8090 is accessible")
	}
	
	if len(analysis.AudioIssues) > 0 {
		fmt.Println("  • Run: agent doctor (for detailed diagnostics)")
		fmt.Println("  • Check jitter_buffer_ms settings")
		fmt.Println("  • Verify network stability")
	}
	
	if len(analysis.Errors) > 10 {
		fmt.Println("  • High error count - check container logs")
		fmt.Println("  • Run: docker logs ai_engine | grep ERROR")
	}
	
	fmt.Println()
}

// displayLLMDiagnosis shows AI-powered diagnosis
func (r *Runner) displayLLMDiagnosis(diagnosis *LLMDiagnosis) {
	fmt.Println("═══════════════════════════════════════════")
	infoColor.Printf("🤖 AI DIAGNOSIS (%s - %s)\n", diagnosis.Provider, diagnosis.Model)
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	fmt.Println(diagnosis.Analysis)
	fmt.Println()
}

// interactiveSession runs interactive troubleshooting
func (r *Runner) interactiveSession(analysis *Analysis) error {
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println("Interactive Mode")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	fmt.Println("Coming soon: Interactive Q&A for deeper diagnosis")
	return nil
}

// Helper functions
func formatDuration(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%ds", int(d.Seconds()))
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	return fmt.Sprintf("%dh", int(d.Hours()))
}

func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}
