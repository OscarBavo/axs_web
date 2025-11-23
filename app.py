from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS
from config import config
import os
import requests
import traceback
import gspread
from google.oauth2.service_account import Credentials
import logging
import json

def create_app(config_name='production'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Habilitar CORS
    CORS(app, resources={
        r"/api/*": {
            "origins": app.config['CORS_ORIGINS'],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })
    
    # ====================
    # MIDDLEWARE
    # ====================
    
    @app.before_request
    def log_request():
        """Log de todas las peticiones"""
        app.logger.info(f'{request.method} {request.path}')
    
    @app.after_request
    def after_request(response):
        """Headers de seguridad"""
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response
    
    

    # Clase Response generic
    class MKTResponse:
        """
        Clase de respuesta estandarizada
        Equivalente a: class MKTResponse<T> en Kotlin/Java
        """
        def __init__(self, es_error=True, error_code="", message=None, result=None):
            self.EsError = es_error
            self.ErrorCode = error_code
            self.Message = message
            self.Result = result
        
        def to_dict(self):
            """Convierte la respuesta a diccionario para JSON"""
            return {
                "EsError": self.EsError,
                "ErrorCode": self.ErrorCode,
                "Message": self.Message,
                "Result": self.Result
            }
        
        @staticmethod
        def success(result, message="Operación exitosa"):
            """Crea una respuesta exitosa"""
            return MKTResponse(
                es_error=False,
                error_code="0",
                message=message,
                result=result
            )
        
        @staticmethod
        def error(error_code, message, result=None):
            """Crea una respuesta de error"""
            return MKTResponse(
                es_error=True,
                error_code=error_code,
                message=message,
                result=result
            )

    # ==============================================================================
    # CONFIGURACIÓN DE GOOGLE SHEETS
    # ==============================================================================

    class GoogleSheetsService:
        def __init__(self, credentials_file='credentials.json'):
            """
            Inicializa el servicio de Google Sheets
            
            Args:
                credentials_file: Ruta al archivo de credenciales JSON
            """
            self.credentials_file = credentials_file
            self.client = None
            self.scopes = [
                'https://www.googleapis.com/auth/spreadsheets.readonly',
                'https://www.googleapis.com/auth/drive.readonly'
            ]
    
        def connect(self):
            """Establece conexión con Google Sheets API"""
            try:
                creds = Credentials.from_service_account_file(
                    self.credentials_file,
                    scopes=self.scopes
                )
                self.client = gspread.authorize(creds)
                return True
            except Exception as e:
                return False
    
    def get_sheet_data(self, sheet_url, worksheet_name=None):
        """
        Obtiene datos de una hoja de cálculo
        
        Args:
            sheet_url: URL o ID del Google Sheet
            worksheet_name: Nombre de la hoja (opcional, usa la primera si no se especifica)
        
        Returns:
            dict: Datos de la hoja incluyendo headers y rows
        """
        try:
            # Abrir el documento
            if 'docs.google.com' in sheet_url:
                sheet = self.client.open_by_url(sheet_url)
            else:
                sheet = self.client.open_by_key(sheet_url)
            
            # Seleccionar la hoja
            if worksheet_name:
                worksheet = sheet.worksheet(worksheet_name)
            else:
                worksheet = sheet.get_worksheet(0)
            
            # Obtener todos los datos
            all_values = worksheet.get_all_values()
            
            errorResponse=MKTResponse.error(error_code=-1,message="sin datos")
            
            if not all_values:
                return {"headers": [], "total": 0, "errorResponse":errorResponse.to_dict()}
            
            # Primera fila como headers
            headers = all_values[0]
            rows = all_values[1:]
            
            # Convertir a lista de diccionarios
            data_dicts = []
            for row in rows:
                row_dict = {}
                for i, header in enumerate(headers):
                    row_dict[header] = row[i] if i < len(row) else ""
                data_dicts.append(row_dict)
                
                        
            errorResponse=MKTResponse.success(result="OK")
            
            return {
                "sheet_name": worksheet.title,
                "headers": headers,
                "inventoryList": data_dicts,
                "errorResponse":errorResponse.to_dict(),
                "total": len(rows)
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def get_all_worksheets(self, sheet_url):
        """Obtiene lista de todas las hojas en el documento"""
        try:
            if 'docs.google.com' in sheet_url:
                sheet = self.client.open_by_url(sheet_url)
            else:
                sheet = self.client.open_by_key(sheet_url)
            
            worksheets = sheet.worksheets()
            return [ws.title for ws in worksheets]
        except Exception as e:
            logger.error(f"Error al obtener hojas: {e}")
            return []


    # Inicializar el servicio
    sheets_service = GoogleSheetsService()
    
    # ====================
    # INTEGRACIÓN CON NODE.JS
    # ====================
    
    @app.route('/api/axs/belfort/get-node-config', methods=['GET'])
    def get_node_config():
        """Obtener configuración desde Node.js API"""
        try:
            node_url = app.config['NODE_API_URL']
            response = requests.get(
                f'{node_url}/api/config',
                timeout=5
            )
            
            if response.status_code == 200:
                return jsonify({
                    'success': True,
                    'node_config': response.json()
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'error': f'Node.js respondió con status {response.status_code}'
                }), response.status_code
                
        except requests.exceptions.RequestException as e:
            return jsonify({
                'success': False,
                'error': f'Error al conectar con Node.js: {str(e)}'
            }), 503
    
    @app.route('/api/axs/belfort/combined-process', methods=['POST'])
    def combined_process():
        """Procesar datos combinando info de Node.js"""
        try:
            # Obtener datos del request
            data = request.get_json()
            
            # Obtener configuración de Node.js
            node_url = app.config['NODE_API_URL']
            config_response = requests.get(
                f'{node_url}/api/config',
                timeout=5
            )
            
            if config_response.status_code != 200:
                return jsonify({
                    'success': False,
                    'error': 'No se pudo obtener configuración de Node.js'
                }), 503
            
            node_config = config_response.json()
            
            # Procesar datos usando la configuración
            max_items = node_config.get('max_connections', 100)
            
            processed = {
                'data': data,
                'max_items_allowed': max_items,
                'items_processed': len(data.get('items', [])),
                'within_limits': len(data.get('items', [])) <= max_items
            }
            
            return jsonify({
                'success': True,
                'result': processed,
                'node_config_used': node_config
            }), 200
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    # ====================
    # MANEJO DE ERRORES
    # ====================
    
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            'success': False,
            'error': 'Endpoint no encontrado',
            'path': request.path
        }), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        app.logger.error(f'Error 500: {str(error)}')
        return jsonify({
            'success': False,
            'error': 'Error interno del servidor'
        }), 500
    
    @app.errorhandler(Exception)
    def handle_exception(e):
        app.logger.error(f'Excepción no manejada: {str(e)}')
        app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Error inesperado en el servidor'
        }), 500
    
    # ====================
    # FUNCIONES AUXILIARES
    # ====================
    
    def check_node_api():
        """Verificar si Node.js API está disponible"""
        try:
            node_url = app.config['NODE_API_URL']
            response = requests.get(f'{node_url}/api/health', timeout=2)
            return response.status_code == 200
        except:
            return False
    
    return app

# ====================
# PUNTO DE ENTRADA
# ====================

if __name__ == '__main__':
    env = os.getenv('FLASK_ENV', 'production')
    app = create_app(env)
    port = int(os.getenv('PORT', 5000))
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=(env == 'development')
    )