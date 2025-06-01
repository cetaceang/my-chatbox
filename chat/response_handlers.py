import json
import logging

logger = logging.getLogger(__name__)

class ResponseExtractionError(Exception):
    """自定义异常，用于响应内容提取过程中的错误。"""
    pass

def _extract_from_openai_json(response_data):
    """从标准的 OpenAI JSON 结构中提取内容。"""
    try:
        if 'choices' in response_data and len(response_data['choices']) > 0:
            if 'message' in response_data['choices'][0] and 'content' in response_data['choices'][0]['message']:
                content = response_data['choices'][0]['message']['content']
                if content is not None:
                    logger.info("使用标准 OpenAI {'choices': [{'message': {'content': ...}}]} 结构提取了内容。")
                    return content
    except (KeyError, IndexError, TypeError) as e:
        logger.debug(f"使用 OpenAI 结构提取内容失败: {e}")
    return None

def _extract_from_anthropic_json(response_data):
    """从类似 Anthropic 的 JSON 结构中提取内容。"""
    try:
        if 'content' in response_data and isinstance(response_data['content'], list) and len(response_data['content']) > 0:
            first_content_item = response_data['content'][0]
            if isinstance(first_content_item, dict) and first_content_item.get('type') == 'text' and 'text' in first_content_item:
                content = first_content_item['text']
                if content is not None:
                    logger.info("使用类似 Anthropic {'content': [{'type': 'text', 'text': ...}]} 结构提取了内容。")
                    return content
    except (KeyError, IndexError, TypeError) as e:
        logger.debug(f"使用类似 Anthropic 结构提取内容失败: {e}")
    return None

def handle_json_response(response_data):
    """
    尝试使用已知的结构从解析后的 JSON 响应中提取内容。
    """
    # 首先尝试类似 Anthropic 的结构
    content = _extract_from_anthropic_json(response_data)
    if content is not None:
        return content

    # 如果 Anthropic 失败，尝试 OpenAI 结构
    content = _extract_from_openai_json(response_data)
    if content is not None:
        return content

    # 如果两种结构都无效
    logger.error(f"无法使用已知结构从解析的 JSON 响应中提取内容: {str(response_data)[:500]}...")
    raise ResponseExtractionError("未能从 AI 服务的 JSON 响应中提取有效内容。")


def handle_text_stream_response(response_text, content_type):
    """
    从 text/event-stream 或 text/plain 响应中累积内容。
    """
    logger.warning(f"当 stream=False 时收到意外的 Content-Type '{content_type}'。作为流处理以累积内容。")
    accumulated_content = ""
    try:
        for line in response_text.splitlines():
            line = line.strip()
            if not line:
                continue # 跳过空行
            if line.startswith('data:'):
                json_data_str = line[len('data:'):].strip()
                if json_data_str.upper() == '[DONE]':
                    logger.info("在流中收到 [DONE] 标记。")
                    break # 流结束标记

                if not json_data_str:
                    logger.warning("在流中收到空数据行。")
                    continue

                try:
                    chunk_data = json.loads(json_data_str)
                    # 基于常见的流格式 (delta.content) 提取内容
                    content_part = None
                    if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                        delta = chunk_data['choices'][0].get('delta', {})
                        content_part = delta.get('content')

                    if content_part:
                        accumulated_content += content_part
                    # else: logger.debug(f"在流块中未找到内容: {json_data_str[:100]}...")

                except json.JSONDecodeError as chunk_err:
                    logger.error(f"从流解码 JSON 块失败: {chunk_err} - 块: {json_data_str[:200]}...")
                    continue # 记录并继续
            else:
                logger.warning(f"在流中收到非数据行: {line[:100]}...")

        if not accumulated_content:
            logger.error(f"未能从 {content_type} 流中累积任何内容。完整响应文本: {response_text[:500]}...")
            raise ResponseExtractionError(f"未能从 AI 服务的 {content_type} 响应流中提取有效内容。")

        logger.info(f"成功从 {content_type} 流中累积了内容。")
        return accumulated_content

    except Exception as stream_proc_err:
        logger.error(f"处理 {content_type} 流时出错: {stream_proc_err}")
        logger.error(f"响应文本为: {response_text[:500]}...")
        raise ResponseExtractionError(f"处理 AI 服务的 {content_type} 响应流时出错。") from stream_proc_err


async def extract_response_content(response):
    """
    从 aiohttp 响应中提取文本内容，处理不同的
    内容类型和已知的 JSON 结构。

    参数:
        response: aiohttp ClientResponse 对象。

    返回:
        提取的文本内容（字符串）。

    抛出:
        ResponseExtractionError: 如果无法提取或解析内容。
        Exception: 其他在响应处理期间发生的意外错误。
    """
    actual_content_type = response.content_type
    charset = response.charset or 'utf-8' # 如果未指定，默认为 utf-8
    logger.info(f"收到 Content-Type 为 {actual_content_type}，Charset 为 {charset} 的响应")

    try:
        if actual_content_type == 'application/json':
            try:
                # 首先读取原始字节，然后使用检测到的/默认的字符集解码
                raw_body = await response.read()
                response_data = json.loads(raw_body.decode(charset))
                logger.info("正在处理标准的 application/json 响应。")
                return handle_json_response(response_data)
            except json.JSONDecodeError as json_err:
                # 尝试获取文本用于记录，必要时回退
                try:
                    response_text = raw_body.decode(charset)
                except Exception:
                    response_text = "[无法解码响应体]"
                logger.error(f"从 application/json 响应解码 JSON 失败: {json_err}")
                logger.error(f"响应文本为: {response_text[:500]}...")
                raise ResponseExtractionError("AI 服务返回了 application/json 但无法解析。") from json_err
            except Exception as e: # 捕获 JSON 处理期间的其他错误
                 logger.error(f"处理 application/json 响应时出错: {e}")
                 raise ResponseExtractionError("处理 application/json 响应时出错。") from e

        elif actual_content_type in ['text/event-stream', 'text/plain']:
            # 使用检测到的/默认的字符集直接读取文本
            response_text = await response.text(encoding=charset)
            return handle_text_stream_response(response_text, actual_content_type)

        else:
            # 处理其他意外的内容类型
            try:
                response_text = await response.text(encoding=charset)
            except Exception:
                 response_text = "[无法解码响应体]"
            error_msg = f"AI 服务返回了非预期的内容类型: {actual_content_type} - Body: {response_text[:500]}..."
            logger.error(error_msg)
            raise ResponseExtractionError(error_msg)

    except ResponseExtractionError:
        raise # 重新抛出特定的提取错误
    except Exception as e:
        # 捕获响应读取/处理期间的任何其他意外错误
        logger.error(f"提取响应内容时发生意外错误: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise ResponseExtractionError("提取响应内容时发生意外错误。") from e
