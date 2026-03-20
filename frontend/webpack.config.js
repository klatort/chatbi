const path = require('path');
const { ModuleFederationPlugin } = require('webpack').container;
const HtmlWebpackPlugin = require('html-webpack-plugin');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');

module.exports = (env, argv) => {
    const isDev = argv.mode === 'development';

    return {
        entry: './src/index',
        mode: argv.mode || 'development',
        devtool: isDev ? 'eval-source-map' : 'source-map',

        output: {
            path: path.resolve(__dirname, 'dist'),
            filename: '[name].[contenthash:8].js',
            publicPath: 'auto',
            clean: true,
        },

        resolve: {
            extensions: ['.tsx', '.ts', '.js', '.jsx', '.json'],
            alias: {
                '@': path.resolve(__dirname, 'src'),
            },
        },

        module: {
            rules: [
                {
                    test: /\.tsx?$/,
                    use: 'ts-loader',
                    exclude: /node_modules/,
                },
                {
                    test: /\.css$/,
                    use: [
                        isDev ? 'style-loader' : MiniCssExtractPlugin.loader,
                        'css-loader',
                        'postcss-loader',
                    ],
                },
            ],
        },

        plugins: [
            new ModuleFederationPlugin({
                name: 'chatbi_native',
                filename: 'remoteEntry.js',
                exposes: {
                    './ChatBIPanel': './src/bootstrap',
                },
                shared: {
                    react: {
                        singleton: true,
                        requiredVersion: '^18.0.0',
                        eager: false,
                    },
                    'react-dom': {
                        singleton: true,
                        requiredVersion: '^18.0.0',
                        eager: false,
                    },
                },
            }),

            new HtmlWebpackPlugin({
                template: path.resolve(__dirname, 'public/index.html'),
            }),

            ...(isDev
                ? []
                : [
                    new MiniCssExtractPlugin({
                        filename: 'css/[name].[contenthash:8].css',
                    }),
                ]),
        ],

        devServer: {
            port: 3099,
            hot: true,
            headers: {
                'Access-Control-Allow-Origin': '*',
            },
            historyApiFallback: true,
        },

        optimization: {
            splitChunks: false,
        },
    };
};
