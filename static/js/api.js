// API client for Serendipity backend
import { signData, wifToAddress } from './bitcoin.js';

// Get settings from localStorage
export function getSettings() {
    return {
        serverAddress: localStorage.getItem('serendipity_server_address') || '192.168.0.124',
        serverPort: localStorage.getItem('serendipity_server_port') || '42069',
        websocketPort: localStorage.getItem('serendipity_websocket_port') || '8765',
        agentName: localStorage.getItem('serendipity_agent_name') || 'Serendipity',
        folderName: localStorage.getItem('serendipity_folder_name') || 'default',
        openvoiceServer: localStorage.getItem('serendipity_openvoice_server') || '',
        autoPlayVoice: localStorage.getItem('serendipity_auto_play') === 'true',
        activeVoice: localStorage.getItem('serendipity_active_voice') || ''
    };
}

export function saveSettings(settings) {
    if (settings.serverAddress) localStorage.setItem('serendipity_server_address', settings.serverAddress);
    if (settings.serverPort) localStorage.setItem('serendipity_server_port', settings.serverPort);
    if (settings.websocketPort) localStorage.setItem('serendipity_websocket_port', settings.websocketPort);
    if (settings.agentName) localStorage.setItem('serendipity_agent_name', settings.agentName);
    if (settings.folderName) localStorage.setItem('serendipity_folder_name', settings.folderName);
    if (settings.openvoiceServer !== undefined) localStorage.setItem('serendipity_openvoice_server', settings.openvoiceServer);
    if (settings.autoPlayVoice !== undefined) localStorage.setItem('serendipity_auto_play', settings.autoPlayVoice);
    if (settings.activeVoice !== undefined) localStorage.setItem('serendipity_active_voice', settings.activeVoice);
}

export function getBaseUrl() {
    const settings = getSettings();
    return `http://${settings.serverAddress}:${settings.serverPort}`;
}

// GET requests (no auth needed)
export async function getAgents() {
    const response = await fetch(`${getBaseUrl()}/api/GetAgents`);
    return response.json();
}

export async function getAgent(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetAgent?agent_name=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getFolders(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetFolders?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getConversations(agentName, folderName) {
    const response = await fetch(`${getBaseUrl()}/api/GetConversations?agent=${encodeURIComponent(agentName)}&folder_name=${encodeURIComponent(folderName)}`);
    return response.json();
}

export async function getConversation(agentName, folderName, conversationId) {
    const response = await fetch(`${getBaseUrl()}/api/GetConversation?agent=${encodeURIComponent(agentName)}&folder_name=${encodeURIComponent(folderName)}&conversation_id=${encodeURIComponent(conversationId)}`);
    return response.json();
}

export async function getModels() {
    const response = await fetch(`${getBaseUrl()}/api/GetModels`);
    return response.json();
}

export async function getWorkflows(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetWorkflows?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getWorkflow(agentName, workflowId) {
    const response = await fetch(`${getBaseUrl()}/api/GetWorkflow?agent=${encodeURIComponent(agentName)}&workflow_id=${encodeURIComponent(workflowId)}`);
    return response.json();
}

export async function getAvailableVoices(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetAvailableVoices?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getMCPServers() {
    const response = await fetch(`${getBaseUrl()}/api/GetMCPServers`);
    return response.json();
}

export async function getMCPServerConfig(serverName) {
    const response = await fetch(`${getBaseUrl()}/api/GetMCPServerConfig?server_name=${encodeURIComponent(serverName)}`);
    return response.json();
}

export async function getToolsets() {
    const response = await fetch(`${getBaseUrl()}/api/GetToolsets`);
    return response.json();
}

export async function getToolset(toolsetName) {
    const response = await fetch(`${getBaseUrl()}/api/GetToolset?toolset_name=${encodeURIComponent(toolsetName)}`);
    return response.json();
}

export async function getTodo(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetTodo?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getWishlist(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetWishlist?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getVariables(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetVariables?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getWorkspace(agentName) {
    const response = await fetch(`${getBaseUrl()}/api/GetWorkspace?agent=${encodeURIComponent(agentName)}`);
    return response.json();
}

export async function getLLMs() {
    const response = await fetch(`${getBaseUrl()}/spellbook/llms`);
    return response.json();
}

export async function getLLMConfig(llmName) {
    const response = await fetch(`${getBaseUrl()}/spellbook/llms/${encodeURIComponent(llmName)}`);
    return response.json();
}

// POST requests (require signed data)
export async function addMessage(wif, data) {
    // Convert to snake_case keys as expected by the API
    const apiData = {
        conversation_id: data.conversationId || data.conversation_id || '',
        sender: data.sender,
        respond_as: data.respondAs || data.respond_as,
        content: data.content,
        agent: data.agent,
        folder_name: data.folderName || data.folder_name,
        enabled_tools: data.enabledTools || data.enabled_tools || [],
        enabled_roles: data.enabledRoles || data.enabled_roles || [],
        memory: data.memory || 0,
        temperature: data.temperature,
        model_name: data.modelName || data.model_name,
        run: data.run,
        files: data.files || []
    };
    
    const signedData = await signData(apiData, wif);
    const response = await fetch(`${getBaseUrl()}/api/AddMessage/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function editAgent(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/EditAgent/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function editConversation(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/EditConversation/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function deleteConversation(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/DeleteConversation/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function stageFile(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/StageFile/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function removeStagedFile(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/RemoveStagedFile/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function startWorkflow(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/StartWorkflow/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function stopGeneration(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/StopGeneration/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function saveMCPServerConfig(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/SaveMCPServerConfig/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function deleteMCPServerConfig(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/DeleteMCPServerConfig/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function saveToolset(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/SaveToolset/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function deleteToolset(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/DeleteToolset/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function saveLLMConfig(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/SaveLLMConfig/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function deleteLLMConfig(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/DeleteLLMConfig/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function saveDefaultLLM(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/SaveDefaultLLM/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

export async function editAgentRole(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/EditAgentRole/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Spellbook API
export async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${getBaseUrl()}/spellbook/upload`, {
        method: 'POST',
        body: formData
    });
    return response.json();
}

export async function transcribeAudio(audioBlob) {
    const formData = new FormData();
    formData.append('file', audioBlob, 'audio.webm');
    const response = await fetch(`${getBaseUrl()}/spellbook/transcribe`, {
        method: 'POST',
        body: formData
    });
    return response.json();
}

// Archive conversation
export async function archiveConversation(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/ArchiveConversation/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Summarize conversation
export async function summarizeConversation(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/SummarizeConversation/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Generate memory
export async function generateMemory(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/GenerateMemory/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Update knowledge base
export async function updateKnowledgeBase(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/UpdateKnowledgeBase/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Move to folder
export async function moveToFolder(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/MoveToFolder/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Initialize agent voices on OpenVoice server
export async function initializeAgentVoices(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/InitializeAgentVoices/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Generate workflow from instructions
export async function generateWorkflow(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/GenerateWorkflow/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Edit workflow step
export async function editWorkflowStep(wif, data) {
    const signedData = await signData(data, wif);
    const response = await fetch(`${getBaseUrl()}/api/EditWorkflowStep/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(signedData)
    });
    return response.json();
}

// Generate TTS audio URL from OpenVoice server
export function getTTSUrl(openvoiceServer, text, voice, speed = 1.0) {
    if (!openvoiceServer || !text || !voice) return null;
    const encodedText = encodeURIComponent(text);
    const encodedVoice = encodeURIComponent(voice);
    return `${openvoiceServer}/synthesize/?text=${encodedText}&voice=${encodedVoice}&speed=${speed}`;
}

// WebSocket connection for streaming
export function createStreamingConnection(conversationId, onMessage, onError, onClose) {
    const settings = getSettings();
    const wsUrl = `ws://${settings.serverAddress}:${settings.websocketPort}`;
    
    const ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        console.log('WebSocket connected');
        // Send conversation ID to subscribe to updates
        ws.send(JSON.stringify({ type: 'subscribe', conversation_id: conversationId }));
    };
    
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            onMessage(data);
        } catch (e) {
            onMessage({ type: 'text', content: event.data });
        }
    };
    
    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        if (onError) onError(error);
    };
    
    ws.onclose = () => {
        console.log('WebSocket closed');
        if (onClose) onClose();
    };
    
    return ws;
}
