.PHONY: all test exhibit
all: test
test:                ## promotion-gate validator + rejection tests
	cd gate && python3 validate_promotion.py
	cd gate && python3 test_gate.py
exhibit:             ## regenerate the latency-hierarchy figure from its data
	cd exhibit && python3 latency_ladder.py
