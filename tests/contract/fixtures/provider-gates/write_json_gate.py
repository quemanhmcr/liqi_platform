import json,sys
output=sys.argv[sys.argv.index('--output')+1]
json.dump({'schema_version':'fixture-provider-output-v0','passed':True},open(output,'w',encoding='utf-8'))
