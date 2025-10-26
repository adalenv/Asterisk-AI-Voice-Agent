package main

import (
	"fmt"
	"os"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/health"
	"github.com/spf13/cobra"
)

var (
	doctorFix    bool
	doctorJSON   bool
	doctorFormat string
)

var doctorCmd = &cobra.Command{
	Use:   "doctor",
	Short: "System health check and diagnostics",
	Long: `Run comprehensive health checks on the Asterisk AI Voice Agent system.

Checks include:
  - Docker containers and services
  - Asterisk ARI connectivity
  - AudioSocket availability
  - Configuration validation
  - Provider API keys and connectivity
  - Audio pipeline status
  - Recent call history

Exit codes:
  0 - All checks passed
  1 - Warnings detected (non-critical)
  2 - Failures detected (critical)`,
	RunE: func(cmd *cobra.Command, args []string) error {
		checker := health.NewChecker(verbose)
		
		// Run health checks
		result, err := checker.RunAll()
		if err != nil {
			return fmt.Errorf("health check failed: %w", err)
		}
		
		// Output results
		if doctorJSON {
			return result.OutputJSON(os.Stdout)
		}
		
		result.OutputText(os.Stdout)
		
		// Exit with appropriate code
		if result.CriticalCount > 0 {
			os.Exit(2)
		} else if result.WarnCount > 0 {
			os.Exit(1)
		}
		
		return nil
	},
}

func init() {
	doctorCmd.Flags().BoolVar(&doctorFix, "fix", false, "attempt to auto-fix issues")
	doctorCmd.Flags().BoolVar(&doctorJSON, "json", false, "output results as JSON")
	doctorCmd.Flags().StringVar(&doctorFormat, "format", "text", "output format: text|json|markdown")
	
	rootCmd.AddCommand(doctorCmd)
}
