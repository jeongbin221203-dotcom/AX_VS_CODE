// 챗봇 열기/닫기 기능
const chatBtn = document.getElementById('chatBtn');
const chatWindow = document.getElementById('chatWindow');
const closeChat = document.getElementById('closeChat');

if (chatBtn && chatWindow && closeChat) {
    chatBtn.addEventListener('click', () => {
        chatWindow.style.display = 'block';
        chatBtn.style.display = 'none';
    });

    closeChat.addEventListener('click', () => {
        chatWindow.style.display = 'none';
        chatBtn.style.display = 'flex';
    });
}

// 챗봇 대화 로직
function sendReply(optionType, userText) {
    const chatMessages = document.getElementById('chatMessages');
    const chatOptions = document.getElementById('chatOptions');

    // 사용자가 누른 버튼 숨기기
    if (chatOptions) { chatOptions.style.display = 'none'; }
    
    // 사용자 질문 말풍선 추가
    chatMessages.innerHTML += `<div class="user-message">${userText}</div>`;
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // 0.6초 딜레이 후 챗봇 답변 띄우기
    setTimeout(() => {
        let reply = "";
        if (optionType === 1) {
            reply = "제품 단가 및 견적은 <b>영업본부(032-123-4567)</b>로 문의 바랍니다.";
        } else if (optionType === 2) {
            reply = "홈페이지 상단 '대리점 안내'에서 전국 공식 대리점을 확인하실 수 있습니다.";
        } else {
            reply = "전문 수의사가 방문하는 <b>무료 사양 컨설팅</b>은 지역 영업소로 신청해 주세요.";
        }
        
        chatMessages.innerHTML += `<div class="bot-message">${reply}</div><button class="chat-option" style="margin-top: 10px;" onclick="resetChat()">처음 메뉴로 돌아가기</button>`;
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }, 600);
}

// 챗봇 대화 초기화 (처음으로 돌아가기)
function resetChat() {
    const chatMessages = document.getElementById('chatMessages');
    chatMessages.innerHTML = `
        <div class="bot-message">다른 궁금한 점이 있으신가요?</div>
        <div id="chatOptions">
            <button class="chat-option" onclick="sendReply(1, '제품 단가 및 견적 문의')">1. 제품 단가 및 견적 문의</button>
            <button class="chat-option" onclick="sendReply(2, '가까운 대리점 찾기')">2. 가까운 대리점 찾기</button>
            <button class="chat-option" onclick="sendReply(3, '농가 사양 컨설팅 신청')">3. 농가 사양 컨설팅 신청</button>
        </div>
    `;
}