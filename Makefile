.PHONY: install uninstall test lint

install:
	./install.sh

uninstall:
	./uninstall.sh

test:
	python3 -m unittest discover -s tests -v

lint:
	python3 -m py_compile src/apple_hid_battery_override.py
	sh -n install.sh
	sh -n uninstall.sh
