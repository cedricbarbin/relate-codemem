import yaml
def read_yaml(path):
    with open(path) as fh:
        return yaml.safe_load(fh)
