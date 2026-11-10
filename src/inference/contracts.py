"""Remove schema annotations only; retain every actual validation constraint."""
import copy

def compact_contract(schema):
    node=copy.deepcopy(schema)
    def visit(value):
        if isinstance(value,dict):
            for key in ['title','description','examples','default']:
                value.pop(key,None)
            for key,child in value.items():
                # Keys below properties/$defs are user-defined field names, not annotations.
                if key in {'properties','$defs'}:
                    for definition in child.values():visit(definition)
                else:visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(node)
    return node


def bounded_discovery_contract(schema):
    """A stricter generation subset; the original business validator stays intact."""
    node=copy.deepcopy(schema)
    limits={'judgments':1,'support_facts':1,'evidence_refs':2,'spatial_anchors':2,'limitations':2}
    def visit(value):
        if isinstance(value,dict):
            for field,definition in value.get('properties',{}).items():
                if not isinstance(definition,dict):continue
                if field in limits and definition.get('type')=='array':
                    definition['maxItems']=min(definition.get('maxItems',limits[field]),limits[field])
                    if field=='limitations' and definition.get('items',{}).get('type')=='string':
                        definition['items']['maxLength']=min(80,definition['items'].get('maxLength',80))
                if field in {'reason','observed_action'} and definition.get('type')=='string':
                    definition['maxLength']=min(100,definition.get('maxLength',100))
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(node)
    return node
