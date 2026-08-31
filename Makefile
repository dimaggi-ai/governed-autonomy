.PHONY: all test exhibit validation
all: test
test: validation     ## promotion-gate validator + rejection tests + validation
	cd gate && python3 validate_promotion.py
	cd gate && python3 test_gate.py
validation:          ## ladder quoting-fidelity + synthetic records through the gate
	cd exhibit && python3 validate_ladder.py
	cd gate && python3 synthetic_gate_check.py
exhibit:             ## regenerate the latency-hierarchy figure from its data
	cd exhibit && python3 latency_ladder.py
