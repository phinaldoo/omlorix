package main

import (
	_ "embed"
	"encoding/json"
	"fmt"
)

//go:embed server-management-contract.json
var serverManagementContractJSON []byte

type logManagementContract struct {
	DefaultLines           int `json:"defaultLines"`
	MinimumLines           int `json:"minimumLines"`
	MaximumLines           int `json:"maximumLines"`
	MaximumTimeBoundLength int `json:"maximumTimeBoundLength"`
}

type managementContract struct {
	Logs logManagementContract `json:"logs"`
}

var serverManagement = mustLoadManagementContract()

func mustLoadManagementContract() managementContract {
	var contract managementContract
	if err := json.Unmarshal(serverManagementContractJSON, &contract); err != nil {
		panic(fmt.Sprintf("load server-management contract: %v", err))
	}
	if contract.Logs.MinimumLines < 1 ||
		contract.Logs.DefaultLines < contract.Logs.MinimumLines ||
		contract.Logs.DefaultLines > contract.Logs.MaximumLines ||
		contract.Logs.MaximumTimeBoundLength < 1 {
		panic("load server-management contract: invalid log bounds")
	}
	return contract
}

func validateLogLineCount(lines int) error {
	limits := serverManagement.Logs
	if lines < limits.MinimumLines || lines > limits.MaximumLines {
		return fmt.Errorf(
			"--lines must be an integer from %d to %d",
			limits.MinimumLines,
			limits.MaximumLines,
		)
	}
	return nil
}

// normalizeLogLineCount mirrors the Launcher's trusted manager boundary:
// invalid lower values fail, while oversized internal requests are capped.
func normalizeLogLineCount(lines int) (int, error) {
	if lines < serverManagement.Logs.MinimumLines {
		return 0, validateLogLineCount(lines)
	}
	return min(lines, serverManagement.Logs.MaximumLines), nil
}
