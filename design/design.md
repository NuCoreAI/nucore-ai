
# PURPOSE
This document describe design decisions for a nucore llm integration

# BACKGROUND
Nucore is a smart home automation controller/platform. It offers many services the most important of which are:
- Command/Control devices and get thier status
- Monitor devices
- Manage `groups` and `scenes`
- Manage automation `routines`

Since a large installations may exhuast the context immediately (device names, ids, supported commands/params/tc.), the design uses a rotuer -> intent-handler architecture.

# ROUTER
The role of the router is to:
Use the summary device/routine information embedded in its system message in conjuction with the user query to:
1. If the query can be answered from device/routines databases, do it in Natural language and return
2. If not, if it can be categorized as an intent,
- create an array of intents + associated candidate devices/routines for each intent
3. Otherwise, respond with natural language

Always store the conversation history.
